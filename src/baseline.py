"""
AFT Baseline Discovery
Discovers network configuration across AFT accounts
Supports both integrated mode (with AuthConfig) and standalone mode
"""

import boto3
import json
import yaml
from typing import Dict, List, Optional
from dataclasses import asdict
from collections import defaultdict
from datetime import datetime

from models import (
        VPCBaseline,
        TransitGatewayBaseline,
        RouteTableBaseline,
        SecurityGroupBaseline,
        NetworkACLBaseline,
        SecurityGroupRule,
    )



class BaselineDiscovery:
    """
    Discovers network baseline across AFT accounts.

    Supports two modes:
    - Integrated mode: BaselineDiscovery(auth_config=auth)
    - Standalone mode: BaselineDiscovery(hub_account_id="123", region="us-west-2")
    """

    def __init__(self,
                 auth_config=None,
                 hub_account_id: str = None,
                 region: str = "us-west-2"):
        """
        Initialize BaselineDiscovery.

        Args:
            auth_config: AuthConfig instance (integrated mode)
            hub_account_id: Hub account ID (standalone mode)
            region: AWS region
        """
        self.auth_config = auth_config
        self.hub_account_id = hub_account_id
        self.region = region
        self._hub_session = None  # Lazy initialized

    def _get_hub_session(self, fallback_account_id: str = None) -> boto3.Session:
        """Get hub session, lazy initialized."""
        if self._hub_session:
            return self._hub_session

        if self.auth_config:
            self._hub_session = self.auth_config.get_hub_session(fallback_account_id=fallback_account_id)
        else:
            self._hub_session = boto3.Session(region_name=self.region)

        return self._hub_session

    def _get_session(self, account_id: str) -> boto3.Session:
        """Get session for target account."""
        if self.auth_config:
            return self.auth_config.get_account_session(account_id)
        else:
            # Standalone mode - use default session (for local testing)
            return self._get_hub_session()

    def discover_vpc_baseline(self, ec2_client, vpc_id: str) -> VPCBaseline:
        """Discover VPC configuration."""
        vpcs = ec2_client.describe_vpcs(VpcIds=[vpc_id])
        vpc = vpcs['Vpcs'][0]

        subnets = ec2_client.describe_subnets(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )

        # Extract secondary CIDRs
        cidr_associations = [
            assoc['CidrBlock']
            for assoc in vpc.get('CidrBlockAssociationSet', [])
            if assoc['CidrBlockState']['State'] == 'associated'
        ]

        return VPCBaseline(
            vpc_id=vpc_id,
            cidr_block=vpc['CidrBlock'],
            dns_support=vpc.get('EnableDnsSupport', False),
            dns_hostnames=vpc.get('EnableDnsHostnames', False),
            subnet_count=len(subnets['Subnets']),
            subnet_cidrs=[s['CidrBlock'] for s in subnets['Subnets']],
            availability_zones=list(set(s['AvailabilityZone'] for s in subnets['Subnets'])),
            cidr_block_associations=cidr_associations if cidr_associations else None
        )

    def discover_transit_gateway(self, ec2_client, vpc_id: str) -> Optional[TransitGatewayBaseline]:
        """Discover Transit Gateway attachment."""
        attachments = ec2_client.describe_transit_gateway_vpc_attachments(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )

        if not attachments['TransitGatewayVpcAttachments']:
            return None

        att = attachments['TransitGatewayVpcAttachments'][0]

        # Try to get route table association
        route_table_id = None
        try:
            associations = ec2_client.describe_transit_gateway_route_tables(
                Filters=[
                    {'Name': 'transit-gateway-id', 'Values': [att['TransitGatewayId']]}
                ]
            )
            if associations['TransitGatewayRouteTables']:
                route_table_id = associations['TransitGatewayRouteTables'][0]['TransitGatewayRouteTableId']
        except Exception:
            pass

        return TransitGatewayBaseline(
            tgw_id=att['TransitGatewayId'],
            tgw_attachment_id=att['TransitGatewayAttachmentId'],
            attachment_state=att['State'],
            subnet_ids=att.get('SubnetIds', []),
            route_table_id=route_table_id,
            appliance_mode=att.get('Options', {}).get('ApplianceModeSupport') == 'enable'
        )

    def discover_route_tables(self, ec2_client, vpc_id: str) -> List[RouteTableBaseline]:
        """Discover route table configurations."""
        route_tables = ec2_client.describe_route_tables(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )

        baselines = []
        for rt in route_tables['RouteTables']:
            routes = []
            for route in rt['Routes']:
                route_info = {
                    'destination': route.get('DestinationCidrBlock', route.get('DestinationPrefixListId')),
                    'target': (
                        route.get('GatewayId') or
                        route.get('TransitGatewayId') or
                        route.get('NatGatewayId') or
                        route.get('NetworkInterfaceId') or
                        'local'
                    ),
                    'state': route.get('State', 'active')
                }
                routes.append(route_info)

            associated_subnets = [
                assoc['SubnetId']
                for assoc in rt.get('Associations', [])
                if 'SubnetId' in assoc
            ]

            baselines.append(RouteTableBaseline(
                route_table_id=rt['RouteTableId'],
                main=any(assoc.get('Main', False) for assoc in rt.get('Associations', [])),
                routes=routes,
                associated_subnets=associated_subnets
            ))

        return baselines

    def discover_security_groups(self, ec2_client, vpc_id: str) -> List[SecurityGroupBaseline]:
        """Discover security group configurations."""
        security_groups = ec2_client.describe_security_groups(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )

        baselines = []
        for sg in security_groups['SecurityGroups']:
            # Skip default SG for cleaner baseline
            if sg['GroupName'] == 'default':
                continue

            ingress_rules = []
            for rule in sg.get('IpPermissions', []):
                ingress_rules.append({
                    'protocol': rule.get('IpProtocol'),
                    'from_port': rule.get('FromPort'),
                    'to_port': rule.get('ToPort'),
                    'cidr_blocks': [ip['CidrIp'] for ip in rule.get('IpRanges', [])],
                    'source_sgs': [grp['GroupId'] for grp in rule.get('UserIdGroupPairs', [])]
                })

            egress_rules = []
            for rule in sg.get('IpPermissionsEgress', []):
                egress_rules.append({
                    'protocol': rule.get('IpProtocol'),
                    'from_port': rule.get('FromPort'),
                    'to_port': rule.get('ToPort'),
                    'cidr_blocks': [ip['CidrIp'] for ip in rule.get('IpRanges', [])],
                    'dest_sgs': [grp['GroupId'] for grp in rule.get('UserIdGroupPairs', [])]
                })

            baselines.append(SecurityGroupBaseline(
                group_id=sg['GroupId'],
                group_name=sg['GroupName'],
                ingress_rules=ingress_rules,
                egress_rules=egress_rules
            ))

        return baselines

    def discover_network_acls(self, ec2_client, vpc_id: str) -> List[NetworkACLBaseline]:
        """Discover Network ACL configurations."""
        nacls = ec2_client.describe_network_acls(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )

        baselines = []
        for nacl in nacls['NetworkAcls']:
            ingress_rules = [
                {
                    'rule_number': entry['RuleNumber'],
                    'protocol': entry['Protocol'],
                    'action': entry['RuleAction'],
                    'cidr_block': entry.get('CidrBlock'),
                    'port_range': entry.get('PortRange')
                }
                for entry in nacl.get('Entries', [])
                if not entry['Egress']
            ]

            egress_rules = [
                {
                    'rule_number': entry['RuleNumber'],
                    'protocol': entry['Protocol'],
                    'action': entry['RuleAction'],
                    'cidr_block': entry.get('CidrBlock'),
                    'port_range': entry.get('PortRange')
                }
                for entry in nacl.get('Entries', [])
                if entry['Egress']
            ]

            associated_subnets = [
                assoc['SubnetId']
                for assoc in nacl.get('Associations', [])
            ]

            # Skip default NACLs unless they have custom rules
            if nacl.get('IsDefault') and len(ingress_rules) <= 2:
                continue

            baselines.append(NetworkACLBaseline(
                nacl_id=nacl['NetworkAclId'],
                ingress_rules=ingress_rules,
                egress_rules=egress_rules,
                associated_subnets=associated_subnets
            ))

        return baselines

    def discover_allowed_ports(self, ec2_client, vpc_id: str) -> List[SecurityGroupRule]:
        """Discover allowed ports from security groups - simplified version."""
        security_groups = ec2_client.describe_security_groups(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )

        allowed_rules = []

        for sg in security_groups['SecurityGroups']:
            for rule in sg.get('IpPermissions', []):
                protocol = rule.get('IpProtocol', '-1')
                from_port = rule.get('FromPort', 0)
                to_port = rule.get('ToPort', 65535)

                cidr_blocks = [ip['CidrIp'] for ip in rule.get('IpRanges', [])]

                if cidr_blocks or rule.get('UserIdGroupPairs'):
                    allowed_rules.append(SecurityGroupRule(
                        protocol=protocol,
                        from_port=from_port,
                        to_port=to_port,
                        cidr_blocks=cidr_blocks,
                        description=f"SG:{sg['GroupName']}"
                    ))

        return allowed_rules

    def discover_account_baseline(self, account) -> Optional[Dict]:
        """
        Discover complete baseline for an account.

        Args:
            account: AccountConfig instance or dict with account_id, account_name, vpc_id
        """
        # Handle both AccountConfig and dict
        if hasattr(account, 'account_id'):
            account_id = account.account_id
            account_name = account.account_name
            vpc_id = getattr(account, 'vpc_id', None)
        else:
            account_id = account['account_id']
            account_name = account['account_name']
            vpc_id = account.get('vpc_id')

        print(f"Discovering baseline for {account_name} ({account_id})...")

        try:
            session = self._get_session(account_id)
            ec2 = session.client('ec2')

            # If no VPC ID provided, find the first non-default VPC
            if not vpc_id:
                vpcs = ec2.describe_vpcs(
                    Filters=[{'Name': 'is-default', 'Values': ['false']}]
                )
                if not vpcs['Vpcs']:
                    print(f"  No non-default VPCs found in {account_name}")
                    return None
                vpc_id = vpcs['Vpcs'][0]['VpcId']

            # Discover VPC baseline
            vpc_baseline = self.discover_vpc_baseline(ec2, vpc_id)

            # Discover additional components
            transit_gateway = self.discover_transit_gateway(ec2, vpc_id)
            route_tables = self.discover_route_tables(ec2, vpc_id)
            security_groups = self.discover_security_groups(ec2, vpc_id)
            network_acls = self.discover_network_acls(ec2, vpc_id)
            allowed_ports = self.discover_allowed_ports(ec2, vpc_id)

            baseline = {
                'account_id': account_id,
                'account_name': account_name,
                'region': self.region,
                'vpc': asdict(vpc_baseline),
                'transit_gateway': asdict(transit_gateway) if transit_gateway else None,
                'route_tables': [asdict(rt) for rt in route_tables],
                'security_groups': [asdict(sg) for sg in security_groups],
                'network_acls': [asdict(nacl) for nacl in network_acls],
                'allowed_ports': [asdict(r) for r in allowed_ports],
                'discovered_at': datetime.utcnow().isoformat()
            }

            print(f"  ✓ Discovered VPC {vpc_id}")
            print(f"  ✓ Found {len(route_tables)} route tables")
            print(f"  ✓ Found {len(security_groups)} security groups")
            print(f"  ✓ Found {len(allowed_ports)} security group rules")

            return baseline

        except Exception as e:
            print(f"  ✗ Error: {str(e)}")
            return None

    def scan_all_accounts(self, accounts: List) -> List[Dict]:
        """Scan all accounts and generate baselines."""
        baselines = []

        for account in accounts:
            baseline = self.discover_account_baseline(account)
            if baseline:
                baselines.append(baseline)

        return baselines

    def generate_golden_path(self, baselines: List[Dict]) -> Dict:
        """Generate golden path from discovered baselines."""

        print(f"\nGenerating golden path from {len(baselines)} accounts...")

        # Aggregate common patterns
        common_routes = defaultdict(int)
        common_sg_patterns = defaultdict(int)
        port_patterns = defaultdict(int)

        for baseline in baselines:
            # Count route patterns
            for rt in baseline.get('route_tables', []):
                for route in rt.get('routes', []):
                    if route['destination'] != 'local':
                        route_key = f"{route['destination']} -> {route['target'].split('/')[0]}"
                        common_routes[route_key] += 1

            # Security group patterns (from detailed security_groups)
            for sg in baseline.get('security_groups', []):
                for rule in sg.get('ingress_rules', []):
                    rule_key = f"{rule['protocol']}:{rule.get('from_port', 'all')}-{rule.get('to_port', 'all')}"
                    common_sg_patterns[rule_key] += 1

            # Port patterns (from allowed_ports)
            for rule in baseline.get('allowed_ports', []):
                if rule['protocol'] in ['tcp', 'udp']:
                    for port in range(rule['from_port'], rule['to_port'] + 1):
                        key = f"{rule['protocol']}:{port}"
                        port_patterns[key] += 1

        # Patterns appearing in >50% of accounts
        threshold = len(baselines) * 0.5

        golden_routes = [
            route for route, count in common_routes.items()
            if count >= threshold
        ]

        common_patterns = [
            pattern for pattern, count in port_patterns.items()
            if count >= threshold
        ]

        golden_path = {
            'version': '1.0',
            'generated_at': datetime.utcnow().isoformat(),
            'based_on_accounts': len(baselines),
            'threshold_percentage': 50,

            'expected_configuration': {
                'vpc': {
                    'dns_support': all(b['vpc'].get('dns_support', False) for b in baselines),
                    'dns_hostnames': all(b['vpc'].get('dns_hostnames', False) for b in baselines),
                    'min_subnets': min(b['vpc'].get('subnet_count', 0) for b in baselines),
                    'min_availability_zones': 2
                },

                'transit_gateway': {
                    'required': any(b.get('transit_gateway') for b in baselines),
                    'expected_state': 'available',
                    'appliance_mode': any(
                        b.get('transit_gateway', {}).get('appliance_mode', False)
                        for b in baselines
                        if b.get('transit_gateway')
                    )
                },

                'routes': {
                    'expected_destinations': golden_routes,
                    'description': 'Routes appearing in >50% of accounts'
                },

                'security': {
                    'common_ingress_patterns': common_patterns
                }
            },

            'account_baselines': baselines
        }

        return golden_path

    def export_baseline(self, baselines: List[Dict], golden_path: Dict, output_dir: str = "."):
        """Export baselines and golden path to files."""

        # Export individual baselines
        for baseline in baselines:
            filename = f"{output_dir}/baseline_{baseline['account_name']}_{baseline['account_id']}.json"
            with open(filename, 'w') as f:
                json.dump(baseline, f, indent=2, default=str)
            print(f"Exported: {filename}")

        # Export golden path as YAML
        golden_path_file = f"{output_dir}/golden_path.yaml"
        with open(golden_path_file, 'w') as f:
            yaml.dump(golden_path, f, default_flow_style=False, sort_keys=False)
        print(f"Exported: {golden_path_file}")

        # Export golden path as JSON
        golden_path_json = f"{output_dir}/golden_path.json"
        with open(golden_path_json, 'w') as f:
            json.dump(golden_path, f, indent=2)
        print(f"Exported: {golden_path_json}")

        print(f"\n✓ Baseline discovery complete!")
        print(f"✓ Review and edit {golden_path_file} to refine your golden path")
