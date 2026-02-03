"""
Enhanced Connectivity Discovery
Automatically discovers VPC-to-VPC connectivity patterns from:
1. Transit Gateway route tables
2. VPC Flow Logs (actual traffic patterns)
3. VPC Peering, VPN, PrivateLink

Supports both integrated mode (with AuthConfig) and standalone mode.
"""

import boto3
import time
from typing import Dict, List, Set, Optional
from dataclasses import asdict
from collections import defaultdict
from datetime import datetime, timedelta
import ipaddress

from models import ConnectionType, VPCConnectivityPattern, TGWTopology


class ConnectivityDiscovery:
    """
    Discovers VPC-to-VPC connectivity patterns.

    Supports two modes:
    - Integrated mode: ConnectivityDiscovery(auth_config=auth, hub_account_id=id)
    - Standalone mode: ConnectivityDiscovery(hub_account_id=id) with internal session
    """

    def __init__(self, auth_config=None, hub_account_id: str = None, region: str = "us-west-2",
                 fallback_account_id: str = None):
        """
        Initialize ConnectivityDiscovery.

        Args:
            auth_config: AuthConfig instance (integrated mode)
            hub_account_id: Hub account ID
            region: AWS region
            fallback_account_id: Account ID to use for hub session in profile-pattern mode
        """
        self.auth_config = auth_config
        self.hub_account_id = hub_account_id
        self.region = region
        self._fallback_account_id = fallback_account_id
        self._hub_session = None  # Lazy initialized

    def _get_hub_session(self) -> boto3.Session:
        """Get hub session, lazy initialized."""
        if self._hub_session:
            return self._hub_session

        if self.auth_config:
            self._hub_session = self.auth_config.get_hub_session(fallback_account_id=self._fallback_account_id)
        else:
            self._hub_session = boto3.Session(region_name=self.region)

        return self._hub_session

    @property
    def hub_session(self):
        """Lazy-initialized hub session."""
        return self._get_hub_session()

    def _get_session(self, account_id: str) -> boto3.Session:
        """Get session for target account."""
        if self.auth_config:
            return self.auth_config.get_account_session(account_id)
        else:
            # Standalone mode - use default session
            return self._get_hub_session()

    def discover_vpc_peering_connections(self, accounts: List[Dict]) -> List[Dict]:
        """Discover VPC peering connections across accounts."""
        print("Discovering VPC Peering connections...")

        peering_connections = []
        processed_pcx = set()

        for account in accounts:
            try:
                session = self._get_session(account['account_id'])
                ec2 = session.client('ec2')

                response = ec2.describe_vpc_peering_connections(
                    Filters=[
                        {'Name': 'status-code', 'Values': ['active', 'pending-acceptance']}
                    ]
                )

                for pcx in response['VpcPeeringConnections']:
                    pcx_id = pcx['VpcPeeringConnectionId']

                    if pcx_id in processed_pcx:
                        continue
                    processed_pcx.add(pcx_id)

                    requester = pcx['RequesterVpcInfo']
                    accepter = pcx['AccepterVpcInfo']

                    peering_connections.append({
                        'peering_id': pcx_id,
                        'status': pcx['Status']['Code'],
                        'requester_vpc': requester['VpcId'],
                        'requester_account': requester['OwnerId'],
                        'requester_cidr': requester.get('CidrBlock'),
                        'accepter_vpc': accepter['VpcId'],
                        'accepter_account': accepter['OwnerId'],
                        'accepter_cidr': accepter.get('CidrBlock'),
                        'tags': {tag['Key']: tag['Value'] for tag in pcx.get('Tags', [])}
                    })

            except Exception as e:
                print(f"  ✗ Error discovering peering in {account['account_name']}: {str(e)}")

        print(f"  ✓ Found {len(peering_connections)} VPC peering connections")
        return peering_connections

    def discover_vpn_connections(self, accounts: List[Dict]) -> List[Dict]:
        """Discover VPN connections (site-to-site, client VPN)."""
        print("Discovering VPN connections...")

        vpn_connections = []

        for account in accounts:
            try:
                session = self._get_session(account['account_id'])
                ec2 = session.client('ec2')

                response = ec2.describe_vpn_connections(
                    Filters=[{'Name': 'state', 'Values': ['available']}]
                )

                for vpn in response['VpnConnections']:
                    vpn_connections.append({
                        'vpn_id': vpn['VpnConnectionId'],
                        'type': 'site-to-site',
                        'vpc_id': vpn.get('VpcId'),
                        'customer_gateway_id': vpn['CustomerGatewayId'],
                        'state': vpn['State'],
                        'account_id': account['account_id'],
                        'account_name': account['account_name']
                    })

            except Exception as e:
                print(f"  ✗ Error discovering VPN in {account['account_name']}: {str(e)}")

        print(f"  ✓ Found {len(vpn_connections)} VPN connections")
        return vpn_connections

    def discover_privatelink_connections(self, accounts: List[Dict]) -> List[Dict]:
        """Discover VPC Endpoint Services and Endpoints."""
        print("Discovering PrivateLink connections...")

        privatelink_connections = []

        for account in accounts:
            try:
                session = self._get_session(account['account_id'])
                ec2 = session.client('ec2')

                # VPC Endpoints (consumer side)
                endpoints = ec2.describe_vpc_endpoints(
                    Filters=[{'Name': 'vpc-endpoint-type', 'Values': ['Interface']}]
                )

                for endpoint in endpoints['VpcEndpoints']:
                    privatelink_connections.append({
                        'endpoint_id': endpoint['VpcEndpointId'],
                        'type': 'vpc-endpoint',
                        'vpc_id': endpoint['VpcId'],
                        'service_name': endpoint['ServiceName'],
                        'state': endpoint['State'],
                        'account_id': account['account_id'],
                        'account_name': account['account_name']
                    })

                # VPC Endpoint Services (provider side)
                services = ec2.describe_vpc_endpoint_service_configurations()

                for service in services.get('ServiceConfigurations', []):
                    privatelink_connections.append({
                        'service_id': service['ServiceId'],
                        'type': 'endpoint-service',
                        'service_name': service['ServiceName'],
                        'state': service['ServiceState'],
                        'account_id': account['account_id'],
                        'account_name': account['account_name']
                    })

            except Exception as e:
                print(f"  ✗ Error discovering PrivateLink in {account['account_name']}: {str(e)}")

        print(f"  ✓ Found {len(privatelink_connections)} PrivateLink connections")
        return privatelink_connections

    def discover_tgw_ids_from_accounts(self, accounts: List[Dict]) -> List[str]:
        """Auto-discover TGW IDs from VPC attachments across accounts."""
        print("Auto-discovering Transit Gateways from account VPCs...")

        tgw_ids = set()

        for account in accounts:
            try:
                session = self._get_session(account['account_id'])
                ec2 = session.client('ec2')

                # Find TGW attachments for this account's VPCs
                attachments = ec2.describe_transit_gateway_vpc_attachments(
                    Filters=[{'Name': 'state', 'Values': ['available']}]
                )

                for att in attachments.get('TransitGatewayVpcAttachments', []):
                    tgw_ids.add(att['TransitGatewayId'])

            except Exception as e:
                print(f"  ⚠️  Could not check TGW attachments in {account['account_name']}: {str(e)}")

        if tgw_ids:
            print(f"  ✓ Found {len(tgw_ids)} Transit Gateway(s): {', '.join(tgw_ids)}")
        else:
            print("  ⚠️  No Transit Gateways found attached to account VPCs")

        return list(tgw_ids)

    def discover_tgw_topology(self, tgw_id: str) -> TGWTopology:
        """Discover VPC connectivity from Transit Gateway route tables."""
        ec2 = self.hub_session.client('ec2')

        print(f"Discovering TGW topology for {tgw_id}...")

        # Get TGW details
        tgws = ec2.describe_transit_gateways(TransitGatewayIds=[tgw_id])
        tgw = tgws['TransitGateways'][0]
        tgw_name = next(
            (tag['Value'] for tag in tgw.get('Tags', []) if tag['Key'] == 'Name'),
            tgw_id
        )

        # Get all VPC attachments
        attachments = ec2.describe_transit_gateway_vpc_attachments(
            Filters=[
                {'Name': 'transit-gateway-id', 'Values': [tgw_id]},
                {'Name': 'state', 'Values': ['available']}
            ]
        )

        attachment_details = []
        vpc_to_attachment = {}

        for att in attachments['TransitGatewayVpcAttachments']:
            vpc_id = att['VpcId']
            att_id = att['TransitGatewayAttachmentId']

            attachment_details.append({
                'attachment_id': att_id,
                'vpc_id': vpc_id,
                'vpc_owner_id': att['VpcOwnerId'],
                'subnet_ids': att.get('SubnetIds', [])
            })

            vpc_to_attachment[vpc_id] = att_id

        # Get TGW route tables
        route_tables = ec2.describe_transit_gateway_route_tables(
            Filters=[{'Name': 'transit-gateway-id', 'Values': [tgw_id]}]
        )

        route_table_details = []
        vpc_connectivity = defaultdict(set)

        for rt in route_tables['TransitGatewayRouteTables']:
            rt_id = rt['TransitGatewayRouteTableId']

            associations = ec2.get_transit_gateway_route_table_associations(
                TransitGatewayRouteTableId=rt_id
            )

            routes = ec2.search_transit_gateway_routes(
                TransitGatewayRouteTableId=rt_id,
                Filters=[{'Name': 'state', 'Values': ['active']}]
            )

            associated_vpcs = []
            for assoc in associations.get('Associations', []):
                if assoc.get('ResourceType') == 'vpc':
                    resource_id = assoc.get('ResourceId')
                    associated_vpcs.append(resource_id)

            destination_vpcs = set()
            for route in routes.get('Routes', []):
                att_id = route.get('TransitGatewayAttachments', [{}])[0].get('TransitGatewayAttachmentId')
                if att_id:
                    for vpc_id, vpc_att_id in vpc_to_attachment.items():
                        if vpc_att_id == att_id:
                            destination_vpcs.add(vpc_id)

            for source_vpc in associated_vpcs:
                vpc_connectivity[source_vpc].update(destination_vpcs)

            route_table_details.append({
                'route_table_id': rt_id,
                'associated_vpcs': associated_vpcs,
                'destination_vpcs': list(destination_vpcs),
                'route_count': len(routes.get('Routes', []))
            })

        connectivity_matrix = {
            vpc: list(dests) for vpc, dests in vpc_connectivity.items()
        }

        return TGWTopology(
            tgw_id=tgw_id,
            tgw_name=tgw_name,
            owner_account=tgw['OwnerId'],
            route_tables=route_table_details,
            attachments=attachment_details,
            vpc_connectivity_matrix=connectivity_matrix
        )

    def discover_from_flow_logs(self,
                                vpc_id: str,
                                account_id: str,
                                lookback_hours: int = 24) -> List[Dict]:
        """Discover actual traffic patterns from VPC Flow Logs."""
        session = self._get_session(account_id)
        logs = session.client('logs')
        ec2 = session.client('ec2')

        log_group_name = f"/aws/vpc/flowlogs/{vpc_id}"

        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=lookback_hours)

        query = """
        fields @timestamp, srcAddr, dstAddr, srcPort, dstPort, protocol, action, bytes, packets
        | filter action = "ACCEPT"
        | filter (dstAddr like /^10\\./ or dstAddr like /^172\\.1[6-9]\\./ or dstAddr like /^172\\.2[0-9]\\./ or dstAddr like /^172\\.3[0-1]\\./ or dstAddr like /^192\\.168\\./)
        | stats count(*) as packet_count, sum(bytes) as total_bytes by srcAddr, dstAddr, dstPort, protocol
        | sort packet_count desc
        | limit 100
        """

        try:
            response = logs.start_query(
                logGroupName=log_group_name,
                startTime=int(start_time.timestamp()),
                endTime=int(end_time.timestamp()),
                queryString=query
            )

            query_id = response['queryId']

            while True:
                result = logs.get_query_results(queryId=query_id)
                status = result['status']

                if status == 'Complete':
                    break
                elif status == 'Failed':
                    return []

                time.sleep(2)

            traffic_patterns = []
            for row in result.get('results', []):
                row_dict = {item['field']: item['value'] for item in row}

                dest_ip = row_dict.get('dstAddr')
                dest_vpc = self._find_vpc_by_ip(dest_ip, ec2)

                if dest_vpc and dest_vpc != vpc_id:
                    traffic_patterns.append({
                        'source_vpc': vpc_id,
                        'dest_vpc': dest_vpc,
                        'dest_ip': dest_ip,
                        'protocol': row_dict.get('protocol'),
                        'port': int(row_dict.get('dstPort', 0)),
                        'packet_count': int(row_dict.get('packet_count', 0)),
                        'bytes': int(row_dict.get('total_bytes', 0))
                    })

            return traffic_patterns

        except logs.exceptions.ResourceNotFoundException:
            print(f"  ⚠️  Flow logs not enabled for VPC {vpc_id}")
            return []
        except Exception as e:
            print(f"  ✗ Flow log query error: {str(e)}")
            return []

    def _find_vpc_by_ip(self, ip_address: str, ec2_client) -> Optional[str]:
        """Find which VPC owns a given IP address."""
        try:
            ip = ipaddress.ip_address(ip_address)

            vpcs = ec2_client.describe_vpcs()

            for vpc in vpcs['Vpcs']:
                vpc_cidr = ipaddress.ip_network(vpc['CidrBlock'])
                if ip in vpc_cidr:
                    return vpc['VpcId']

                for assoc in vpc.get('CidrBlockAssociationSet', []):
                    if assoc['CidrBlockState']['State'] == 'associated':
                        cidr = ipaddress.ip_network(assoc['CidrBlock'])
                        if ip in cidr:
                            return vpc['VpcId']

            return None

        except Exception:
            return None

    def _get_allowed_ports_for_vpc(self, vpc_id: str, baselines: List[Dict], direction: str = 'ingress') -> Set[int]:
        """
        Extract allowed ports for a VPC from baseline security group data.

        Args:
            vpc_id: VPC ID to look up
            baselines: List of baseline dicts with security groups/allowed_ports
            direction: 'ingress' for destination VPC, 'egress' for source VPC

        Returns:
            Set of allowed port numbers
        """
        allowed_ports = set()

        if not baselines:
            return allowed_ports

        # Find baseline for this VPC
        baseline = None
        for b in baselines:
            if b and b.get('vpc', {}).get('vpc_id') == vpc_id:
                baseline = b
                break

        if not baseline:
            return allowed_ports

        # Extract ports from security groups
        for sg in baseline.get('security_groups', []):
            rules = sg.get(f'{direction}_rules', [])
            for rule in rules:
                protocol = rule.get('protocol', '-1')
                # Skip non-TCP/UDP protocols for port extraction
                if protocol not in ['tcp', 'udp', '6', '17']:
                    if protocol == '-1':
                        # All traffic allowed - add common ports
                        allowed_ports.update([22, 80, 443, 3306, 5432, 8080, 8443])
                    continue

                from_port = rule.get('from_port')
                to_port = rule.get('to_port')

                if from_port is not None and to_port is not None:
                    # Limit range to avoid huge sets
                    if to_port - from_port <= 1000:
                        allowed_ports.update(range(from_port, to_port + 1))
                    else:
                        # For large ranges, just add the endpoints
                        allowed_ports.add(from_port)
                        allowed_ports.add(to_port)

        # Also check allowed_ports from baseline (simplified list)
        for rule in baseline.get('allowed_ports', []):
            if rule.get('protocol') in ['tcp', 'udp']:
                from_port = rule.get('from_port', 0)
                to_port = rule.get('to_port', 0)
                if from_port and to_port and to_port - from_port <= 1000:
                    allowed_ports.update(range(from_port, to_port + 1))

        return allowed_ports

    def _calculate_allowed_ports(self, source_vpc: str, dest_vpc: str, baselines: List[Dict]) -> Set[int]:
        """
        Calculate allowed ports for a connectivity path based on security groups.

        The allowed ports are the intersection of:
        - Source VPC's egress rules
        - Destination VPC's ingress rules

        Args:
            source_vpc: Source VPC ID
            dest_vpc: Destination VPC ID
            baselines: List of baseline dicts

        Returns:
            Set of ports allowed for this path
        """
        if not baselines:
            return set()

        source_egress = self._get_allowed_ports_for_vpc(source_vpc, baselines, 'egress')
        dest_ingress = self._get_allowed_ports_for_vpc(dest_vpc, baselines, 'ingress')

        # If either side has no rules discovered, return the other side's ports
        # (assume open if we can't determine)
        if not source_egress and not dest_ingress:
            return set()
        elif not source_egress:
            return dest_ingress
        elif not dest_ingress:
            return source_egress

        # Return intersection - ports allowed by both source egress and dest ingress
        return source_egress & dest_ingress

    def build_connectivity_map(self,
                               accounts: List[Dict],
                               tgw_id: str = None,
                               discover_tgw: bool = True,
                               discover_peering: bool = True,
                               discover_vpn: bool = True,
                               discover_privatelink: bool = True,
                               use_flow_logs: bool = True,
                               baselines: List[Dict] = None) -> List[VPCConnectivityPattern]:
        """
        Build complete VPC-to-VPC connectivity map.
        Discovers ALL connection types: TGW, Peering, VPN, PrivateLink

        Args:
            accounts: List of account dicts with account_id, account_name, vpc_id
            tgw_id: Specific TGW ID to discover (if None, auto-discovers from accounts)
            discover_tgw: Whether to discover TGW connectivity
            discover_peering: Whether to discover VPC peering
            discover_vpn: Whether to discover VPN connections
            discover_privatelink: Whether to discover PrivateLink
            use_flow_logs: Whether to analyze flow logs for traffic patterns
            baselines: List of baseline dicts with security groups/allowed_ports per VPC
        """
        print("\n" + "=" * 80)
        print("DISCOVERING ALL CONNECTIVITY TYPES")
        print("=" * 80)

        connectivity_patterns = []
        account_map = {acc['account_id']: acc['account_name'] for acc in accounts}
        vpc_to_account = {acc['vpc_id']: acc for acc in accounts if acc.get('vpc_id')}

        # 1. Transit Gateway Connectivity
        if discover_tgw:
            print("\n[1/4] Transit Gateway Connectivity")

            # Auto-discover TGW IDs if not provided
            if tgw_id:
                tgw_ids = [tgw_id]
            else:
                tgw_ids = self.discover_tgw_ids_from_accounts(accounts)

            for current_tgw_id in tgw_ids:
                try:
                    tgw_topology = self.discover_tgw_topology(current_tgw_id)

                    print(f"  ✓ TGW {current_tgw_id}: {len(tgw_topology.attachments)} VPC attachments, {len(tgw_topology.route_tables)} route tables")

                    # Enrich vpc_to_account with data from TGW attachments
                    for att in tgw_topology.attachments:
                        vpc_id = att.get('vpc_id')
                        if vpc_id and vpc_id not in vpc_to_account:
                            vpc_to_account[vpc_id] = {
                                'account_id': att.get('vpc_owner_id', 'unknown'),
                                'account_name': att.get('vpc_owner_id', 'unknown'),  # Use account ID as name if not in accounts list
                                'vpc_id': vpc_id
                            }

                    for source_vpc, dest_vpcs in tgw_topology.vpc_connectivity_matrix.items():
                        source_acc = vpc_to_account.get(source_vpc, {})

                        for dest_vpc in dest_vpcs:
                            if source_vpc == dest_vpc:
                                continue

                            dest_acc = vpc_to_account.get(dest_vpc, {})

                            connectivity_patterns.append(VPCConnectivityPattern(
                                source_vpc_id=source_vpc,
                                source_account_id=source_acc.get('account_id', 'unknown'),
                                source_account_name=source_acc.get('account_name', 'unknown'),
                                dest_vpc_id=dest_vpc,
                                dest_account_id=dest_acc.get('account_id', 'unknown'),
                                dest_account_name=dest_acc.get('account_name', 'unknown'),
                                connection_type=ConnectionType.TRANSIT_GATEWAY,
                                connection_id=current_tgw_id,
                                expected=True,
                                traffic_observed=False,
                                protocols_observed=set(),
                                ports_observed=set(),
                                ports_allowed=self._calculate_allowed_ports(source_vpc, dest_vpc, baselines),
                                first_seen=datetime.utcnow().isoformat(),
                                last_seen=datetime.utcnow().isoformat(),
                                use_case="general"
                            ))
                except Exception as e:
                    print(f"  ⚠️  Error discovering TGW {current_tgw_id}: {str(e)}")

            tgw_count = sum(1 for p in connectivity_patterns if p.connection_type == ConnectionType.TRANSIT_GATEWAY)
            print(f"  ✓ Discovered {tgw_count} TGW connectivity paths")
        else:
            print("\n[1/4] Transit Gateway Connectivity - SKIPPED")

        # 2. VPC Peering Connectivity
        if discover_peering:
            print("\n[2/4] VPC Peering Connectivity")
            peering_conns = self.discover_vpc_peering_connections(accounts)

            for pcx in peering_conns:
                requester_acc = next((a for a in accounts if a['vpc_id'] == pcx['requester_vpc']), {})
                accepter_acc = next((a for a in accounts if a['vpc_id'] == pcx['accepter_vpc']), {})

                use_case = pcx['tags'].get('UseCase', pcx['tags'].get('Purpose', 'general'))

                for source, dest in [(pcx['requester_vpc'], pcx['accepter_vpc']),
                                     (pcx['accepter_vpc'], pcx['requester_vpc'])]:
                    source_acc = requester_acc if source == pcx['requester_vpc'] else accepter_acc
                    dest_acc = accepter_acc if dest == pcx['accepter_vpc'] else requester_acc

                    connectivity_patterns.append(VPCConnectivityPattern(
                        source_vpc_id=source,
                        source_account_id=source_acc.get('account_id', 'unknown'),
                        source_account_name=source_acc.get('account_name', 'unknown'),
                        dest_vpc_id=dest,
                        dest_account_id=dest_acc.get('account_id', 'unknown'),
                        dest_account_name=dest_acc.get('account_name', 'unknown'),
                        connection_type=ConnectionType.VPC_PEERING,
                        connection_id=pcx['peering_id'],
                        expected=pcx['status'] == 'active',
                        traffic_observed=False,
                        protocols_observed=set(),
                        ports_observed=set(),
                        ports_allowed=self._calculate_allowed_ports(source, dest, baselines),
                        first_seen=datetime.utcnow().isoformat(),
                        last_seen=datetime.utcnow().isoformat(),
                        use_case=use_case
                    ))

            peering_count = sum(1 for p in connectivity_patterns if p.connection_type == ConnectionType.VPC_PEERING)
            print(f"  ✓ Discovered {peering_count} VPC Peering connectivity paths")

        # 3. VPN Connectivity
        if discover_vpn:
            print("\n[3/4] VPN Connectivity")
            vpn_conns = self.discover_vpn_connections(accounts)

            for vpn in vpn_conns:
                if vpn.get('vpc_id'):
                    vpc_acc = next((a for a in accounts if a['vpc_id'] == vpn['vpc_id']), {})

                    connectivity_patterns.append(VPCConnectivityPattern(
                        source_vpc_id=vpn['vpc_id'],
                        source_account_id=vpc_acc.get('account_id', 'unknown'),
                        source_account_name=vpc_acc.get('account_name', 'unknown'),
                        dest_vpc_id='on-premises',
                        dest_account_id='external',
                        dest_account_name='On-Premises',
                        connection_type=ConnectionType.VPN,
                        connection_id=vpn['vpn_id'],
                        expected=vpn['state'] == 'available',
                        traffic_observed=False,
                        protocols_observed=set(),
                        ports_observed=set(),
                        ports_allowed=self._get_allowed_ports_for_vpc(vpn['vpc_id'], baselines, 'egress'),
                        first_seen=datetime.utcnow().isoformat(),
                        last_seen=datetime.utcnow().isoformat(),
                        use_case="hybrid-connectivity"
                    ))

            vpn_count = sum(1 for p in connectivity_patterns if p.connection_type == ConnectionType.VPN)
            print(f"  ✓ Discovered {vpn_count} VPN connectivity paths")

        # 4. PrivateLink Connectivity
        if discover_privatelink:
            print("\n[4/4] PrivateLink Connectivity")
            privatelink_conns = self.discover_privatelink_connections(accounts)

            for pl in privatelink_conns:
                if pl['type'] == 'vpc-endpoint':
                    vpc_acc = next((a for a in accounts if a['vpc_id'] == pl['vpc_id']), {})

                    connectivity_patterns.append(VPCConnectivityPattern(
                        source_vpc_id=pl['vpc_id'],
                        source_account_id=vpc_acc.get('account_id', 'unknown'),
                        source_account_name=vpc_acc.get('account_name', 'unknown'),
                        dest_vpc_id='privatelink-service',
                        dest_account_id='service',
                        dest_account_name=pl['service_name'],
                        connection_type=ConnectionType.PRIVATELINK,
                        connection_id=pl['endpoint_id'],
                        expected=pl['state'] == 'available',
                        traffic_observed=False,
                        protocols_observed=set(),
                        ports_observed=set(),
                        ports_allowed=self._get_allowed_ports_for_vpc(pl['vpc_id'], baselines, 'egress'),
                        first_seen=datetime.utcnow().isoformat(),
                        last_seen=datetime.utcnow().isoformat(),
                        use_case="service-access"
                    ))

            pl_count = sum(1 for p in connectivity_patterns if p.connection_type == ConnectionType.PRIVATELINK)
            print(f"  ✓ Discovered {pl_count} PrivateLink connectivity paths")

        print(f"\n{'=' * 80}")
        print(f"TOTAL CONNECTIVITY PATHS DISCOVERED: {len(connectivity_patterns)}")
        print(f"{'=' * 80}")

        by_type = defaultdict(int)
        for p in connectivity_patterns:
            by_type[p.connection_type.value] += 1

        print("\nBy Connection Type:")
        for conn_type, count in sorted(by_type.items()):
            print(f"  {conn_type.upper()}: {count}")

        # 5. Enhance with Flow Logs
        if use_flow_logs:
            print("\nAnalyzing VPC Flow Logs for actual traffic patterns...")

            traffic_data = defaultdict(lambda: {
                'protocols': set(),
                'ports': set(),
                'packet_count': 0
            })

            for account in accounts:
                vpc_id = account['vpc_id']
                account_id = account['account_id']
                account_name = account.get('account_name', account_id)

                if not vpc_id:
                    print(f"  ⚠️  Skipping flow logs for {account_name} - no VPC discovered")
                    continue

                print(f"  Checking flow logs for {account_name}...")

                traffic = self.discover_from_flow_logs(vpc_id, account_id, lookback_hours=24)

                for t in traffic:
                    key = (t['source_vpc'], t['dest_vpc'])
                    traffic_data[key]['protocols'].add(t['protocol'])
                    traffic_data[key]['ports'].add(t['port'])
                    traffic_data[key]['packet_count'] += t['packet_count']

            for pattern in connectivity_patterns:
                key = (pattern.source_vpc_id, pattern.dest_vpc_id)
                if key in traffic_data:
                    pattern.traffic_observed = True
                    pattern.protocols_observed = traffic_data[key]['protocols']
                    pattern.ports_observed = traffic_data[key]['ports']
                    pattern.packet_count = traffic_data[key]['packet_count']

            observed_count = sum(1 for p in connectivity_patterns if p.traffic_observed)
            print(f"\n✓ Found actual traffic on {observed_count}/{len(connectivity_patterns)} paths")

        return connectivity_patterns

    def save_connectivity_map(self, patterns: List[VPCConnectivityPattern], filename: str, tgw_id: str = None):
        """Save connectivity map to golden path."""
        import yaml

        connectivity_data = {
            'patterns': [
                {
                    'source_vpc_id': p.source_vpc_id,
                    'source_account_id': p.source_account_id,
                    'source_account_name': p.source_account_name,
                    'dest_vpc_id': p.dest_vpc_id,
                    'dest_account_id': p.dest_account_id,
                    'dest_account_name': p.dest_account_name,
                    'connection_type': p.connection_type.value,
                    'connection_id': p.connection_id,
                    'via_tgw': tgw_id if p.connection_type == ConnectionType.TRANSIT_GATEWAY else None,
                    'expected_reachable': p.expected,
                    'traffic_observed': p.traffic_observed,
                    'protocols_observed': list(p.protocols_observed),
                    'ports_observed': sorted(list(p.ports_observed)),
                    'packet_count': p.packet_count,
                    'use_case': p.use_case
                }
                for p in patterns
            ],
            'discovered_at': datetime.utcnow().isoformat(),
            'tgw_id': tgw_id,
            'total_paths': len(patterns),
            'active_paths': sum(1 for p in patterns if p.traffic_observed),
            'by_connection_type': {
                'tgw': sum(1 for p in patterns if p.connection_type == ConnectionType.TRANSIT_GATEWAY),
                'peering': sum(1 for p in patterns if p.connection_type == ConnectionType.VPC_PEERING),
                'vpn': sum(1 for p in patterns if p.connection_type == ConnectionType.VPN),
                'privatelink': sum(1 for p in patterns if p.connection_type == ConnectionType.PRIVATELINK)
            }
        }

        try:
            with open(filename, 'r') as f:
                golden_path = yaml.safe_load(f) or {}
        except FileNotFoundError:
            golden_path = {}

        golden_path['connectivity'] = connectivity_data

        with open(filename, 'w') as f:
            yaml.dump(golden_path, f, default_flow_style=False)

        print(f"\n✓ Connectivity map saved to {filename}")
        print(f"  - TGW paths: {connectivity_data['by_connection_type']['tgw']}")
        print(f"  - Peering paths: {connectivity_data['by_connection_type']['peering']}")
        print(f"  - VPN paths: {connectivity_data['by_connection_type']['vpn']}")
        print(f"  - PrivateLink paths: {connectivity_data['by_connection_type']['privatelink']}")
