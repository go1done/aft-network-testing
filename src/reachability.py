"""
Consolidated Reachability Tester
Supports testing across different connection types:
- Transit Gateway
- VPC Peering
- VPN
- PrivateLink

Uses AWS Reachability Analyzer for network path analysis.
"""

import boto3
import time
from typing import Dict, Optional

from models import ConnectionType, TestResult, TestCase


class ReachabilityTester:
    """
    Unified reachability testing using AWS Reachability Analyzer.
    Adapts testing method based on connection type.
    Ensures idempotence by reusing existing Network Insights Paths.
    """

    def __init__(self, auth_config=None, region: str = "us-west-2"):
        """
        Initialize ReachabilityTester.

        Args:
            auth_config: AuthConfig instance (optional)
            region: AWS region
        """
        self.auth_config = auth_config
        self.region = region
        self._hub_session = None  # Lazy initialized
        self._ec2 = None  # Lazy initialized
        # Cache for existing paths: (source, dest, protocol, port) -> path_id
        self._path_cache: Dict[tuple, str] = {}

    def _get_hub_session(self, fallback_account_id: str = None) -> boto3.Session:
        """Get hub session, lazy initialized."""
        if self._hub_session:
            return self._hub_session

        if self.auth_config:
            self._hub_session = self.auth_config.get_hub_session(fallback_account_id=fallback_account_id)
        else:
            self._hub_session = boto3.Session(region_name=self.region)

        return self._hub_session

    @property
    def ec2(self):
        """Lazy-initialized EC2 client."""
        if self._ec2 is None:
            self._ec2 = self._get_hub_session().client('ec2')
        return self._ec2

    def set_fallback_account(self, account_id: str):
        """Set the fallback account ID for profile-pattern mode."""
        if not self._hub_session and self.auth_config:
            self._hub_session = self.auth_config.get_hub_session(fallback_account_id=account_id)
            self._ec2 = self._hub_session.client('ec2')

    def find_tgw_attachment(self, vpc_id: str, tgw_id: str, session: boto3.Session = None) -> Optional[str]:
        """Find TGW attachment ARN for a VPC."""
        ec2 = session.client('ec2') if session else self.ec2

        attachments = ec2.describe_transit_gateway_vpc_attachments(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
                {'Name': 'transit-gateway-id', 'Values': [tgw_id]},
                {'Name': 'state', 'Values': ['available']}
            ]
        )

        if not attachments['TransitGatewayVpcAttachments']:
            return None

        att = attachments['TransitGatewayVpcAttachments'][0]
        # TransitGatewayOwnerId only present for RAM-shared TGWs, fall back to VpcOwnerId
        owner_id = att.get('TransitGatewayOwnerId') or att.get('VpcOwnerId')
        att_id = att['TransitGatewayAttachmentId']

        return f"arn:aws:ec2:{self.region}:{owner_id}:transit-gateway-attachment/{att_id}"

    def test_reachability(self,
                          source_vpc: str,
                          dest_vpc: str,
                          tgw_id: str,
                          protocol: str = '-1',
                          port: int = None) -> TestCase:
        """
        Test reachability using TGW attachments.
        No ENI creation needed when using TGW attachment ARNs.
        Uses idempotent path creation to avoid duplicates.
        """
        start_time = time.time()

        try:
            source_arn = self.find_tgw_attachment(source_vpc, tgw_id)
            dest_arn = self.find_tgw_attachment(dest_vpc, tgw_id)

            if not source_arn or not dest_arn:
                return TestCase(
                    name=f"Reachability-{protocol}:{port or 'all'}",
                    result=TestResult.SKIP,
                    message="TGW attachments not found",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            # Use idempotent path creation
            path_id = self._get_or_create_path(source_arn, dest_arn, protocol, port)

            analysis = self.ec2.start_network_insights_analysis(
                NetworkInsightsPathId=path_id
            )
            analysis_id = analysis['NetworkInsightsAnalysis']['NetworkInsightsAnalysisId']

            result = self._wait_for_analysis(analysis_id)
            reachable = result.get('NetworkPathFound', False)

            return TestCase(
                name=f"Reachability-{protocol}:{port or 'all'}",
                result=TestResult.PASS if reachable else TestResult.FAIL,
                message=f"Path {'found' if reachable else 'not found'}",
                duration_ms=int((time.time() - start_time) * 1000),
                metadata={
                    'analysis_id': analysis_id,
                    'path_id': path_id,
                    'reachable': reachable
                }
            )

        except Exception as e:
            return TestCase(
                name=f"Reachability-{protocol}:{port or 'all'}",
                result=TestResult.FAIL,
                message=f"Test error: {str(e)}",
                duration_ms=int((time.time() - start_time) * 1000)
            )

    def test_tgw_reachability(self,
                              source_vpc: str,
                              dest_vpc: str,
                              tgw_id: str,
                              protocol: str = '-1',
                              port: int = None) -> TestCase:
        """Test reachability via Transit Gateway."""
        start_time = time.time()

        try:
            source_arn = self._find_tgw_attachment_arn(source_vpc, tgw_id)
            dest_arn = self._find_tgw_attachment_arn(dest_vpc, tgw_id)

            if not source_arn or not dest_arn:
                return TestCase(
                    name=f"TGW-{protocol}:{port or 'all'}",
                    result=TestResult.SKIP,
                    message="TGW attachments not found",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            analysis_id = self._create_reachability_analysis(
                source_arn, dest_arn, protocol, port
            )

            result = self._wait_for_analysis(analysis_id)
            reachable = result.get('NetworkPathFound', False)

            return TestCase(
                name=f"TGW-{protocol}:{port or 'all'}",
                result=TestResult.PASS if reachable else TestResult.FAIL,
                message=f"Path {'found' if reachable else 'not found'}",
                duration_ms=int((time.time() - start_time) * 1000),
                metadata={'analysis_id': analysis_id, 'reachable': reachable}
            )

        except Exception as e:
            return TestCase(
                name=f"TGW-{protocol}:{port or 'all'}",
                result=TestResult.FAIL,
                message=f"Test error: {str(e)}",
                duration_ms=int((time.time() - start_time) * 1000)
            )

    def test_peering_reachability(self,
                                  source_vpc: str,
                                  dest_vpc: str,
                                  peering_id: str,
                                  protocol: str = '-1',
                                  port: int = None) -> TestCase:
        """Test reachability via VPC Peering."""
        start_time = time.time()

        try:
            pcx = self.ec2.describe_vpc_peering_connections(
                VpcPeeringConnectionIds=[peering_id]
            )

            if not pcx['VpcPeeringConnections']:
                return TestCase(
                    name=f"Peering-{protocol}:{port or 'all'}",
                    result=TestResult.SKIP,
                    message=f"Peering {peering_id} not found",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            pcx_status = pcx['VpcPeeringConnections'][0]['Status']['Code']

            if pcx_status != 'active':
                return TestCase(
                    name=f"Peering-{protocol}:{port or 'all'}",
                    result=TestResult.FAIL,
                    message=f"Peering status: {pcx_status} (expected: active)",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            source_eni = self._find_suitable_eni(source_vpc)
            dest_eni = self._find_suitable_eni(dest_vpc)

            if not source_eni or not dest_eni:
                return TestCase(
                    name=f"Peering-{protocol}:{port or 'all'}",
                    result=TestResult.WARN,
                    message="No suitable ENIs found for testing. Peering is active but cannot test reachability.",
                    duration_ms=int((time.time() - start_time) * 1000),
                    metadata={'peering_status': 'active', 'test_skipped': True}
                )

            analysis_id = self._create_reachability_analysis(
                source_eni, dest_eni, protocol, port
            )

            result = self._wait_for_analysis(analysis_id)
            reachable = result.get('NetworkPathFound', False)

            return TestCase(
                name=f"Peering-{protocol}:{port or 'all'}",
                result=TestResult.PASS if reachable else TestResult.FAIL,
                message=f"Path {'found' if reachable else 'not found'} via peering {peering_id}",
                duration_ms=int((time.time() - start_time) * 1000),
                metadata={'analysis_id': analysis_id, 'reachable': reachable}
            )

        except Exception as e:
            return TestCase(
                name=f"Peering-{protocol}:{port or 'all'}",
                result=TestResult.FAIL,
                message=f"Test error: {str(e)}",
                duration_ms=int((time.time() - start_time) * 1000)
            )

    def test_vpn_reachability(self,
                              vpc_id: str,
                              vpn_id: str,
                              protocol: str = '-1',
                              port: int = None) -> TestCase:
        """Test VPN connectivity by validating tunnel status."""
        start_time = time.time()

        try:
            vpn = self.ec2.describe_vpn_connections(
                VpnConnectionIds=[vpn_id]
            )

            if not vpn['VpnConnections']:
                return TestCase(
                    name=f"VPN-{protocol}:{port or 'all'}",
                    result=TestResult.SKIP,
                    message=f"VPN {vpn_id} not found",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            vpn_conn = vpn['VpnConnections'][0]
            state = vpn_conn['State']

            tunnels_up = 0
            total_tunnels = 0

            for options in vpn_conn.get('VgwTelemetry', []):
                total_tunnels += 1
                if options.get('Status') == 'UP':
                    tunnels_up += 1

            if state == 'available' and tunnels_up > 0:
                return TestCase(
                    name=f"VPN-Tunnel-Status",
                    result=TestResult.PASS,
                    message=f"VPN available, {tunnels_up}/{total_tunnels} tunnels UP",
                    duration_ms=int((time.time() - start_time) * 1000),
                    metadata={'tunnels_up': tunnels_up, 'total_tunnels': total_tunnels}
                )
            elif state == 'available':
                return TestCase(
                    name=f"VPN-Tunnel-Status",
                    result=TestResult.WARN,
                    message=f"VPN available but all tunnels DOWN",
                    duration_ms=int((time.time() - start_time) * 1000),
                    metadata={'tunnels_up': 0, 'total_tunnels': total_tunnels}
                )
            else:
                return TestCase(
                    name=f"VPN-Tunnel-Status",
                    result=TestResult.FAIL,
                    message=f"VPN state: {state}",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

        except Exception as e:
            return TestCase(
                name=f"VPN-Tunnel-Status",
                result=TestResult.FAIL,
                message=f"Test error: {str(e)}",
                duration_ms=int((time.time() - start_time) * 1000)
            )

    def test_privatelink_reachability(self,
                                      vpc_id: str,
                                      endpoint_id: str,
                                      protocol: str = 'tcp',
                                      port: int = 443) -> TestCase:
        """
        Test VPC Endpoint connectivity using actual path analysis.

        Unlike status-only checks, this verifies:
        - Security groups allow traffic
        - NACLs allow traffic
        - Route tables have path to endpoint

        Args:
            vpc_id: Source VPC ID
            endpoint_id: VPC Endpoint ID (vpce-xxx)
            protocol: Protocol to test (default: tcp)
            port: Port to test (default: 443)

        Returns:
            TestCase with path analysis result
        """
        start_time = time.time()

        try:
            # Get endpoint details
            endpoint = self.ec2.describe_vpc_endpoints(
                VpcEndpointIds=[endpoint_id]
            )

            if not endpoint['VpcEndpoints']:
                return TestCase(
                    name=f"PrivateLink-{protocol}:{port}",
                    result=TestResult.SKIP,
                    message=f"VPC Endpoint {endpoint_id} not found",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            ep = endpoint['VpcEndpoints'][0]
            state = ep['State']

            # Fail fast if endpoint not available
            if state != 'available':
                return TestCase(
                    name=f"PrivateLink-{protocol}:{port}",
                    result=TestResult.FAIL,
                    message=f"VPC Endpoint state: {state}",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            endpoint_enis = ep.get('NetworkInterfaceIds', [])
            if not endpoint_enis:
                return TestCase(
                    name=f"PrivateLink-{protocol}:{port}",
                    result=TestResult.FAIL,
                    message="VPC Endpoint has no ENIs",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            # Find source ENI in the VPC
            source_eni_arn = self._find_suitable_eni(vpc_id)
            if not source_eni_arn:
                return TestCase(
                    name=f"PrivateLink-{protocol}:{port}",
                    result=TestResult.WARN,
                    message="No source ENI found in VPC for path analysis. Endpoint is available but cannot verify reachability.",
                    duration_ms=int((time.time() - start_time) * 1000),
                    metadata={'state': state, 'endpoint_enis': len(endpoint_enis), 'test_skipped': True}
                )

            # Build destination ENI ARN (use first endpoint ENI)
            dest_eni_id = endpoint_enis[0]
            # Get owner ID from the ENI
            eni_details = self.ec2.describe_network_interfaces(
                NetworkInterfaceIds=[dest_eni_id]
            )
            if not eni_details['NetworkInterfaces']:
                return TestCase(
                    name=f"PrivateLink-{protocol}:{port}",
                    result=TestResult.FAIL,
                    message=f"Could not find endpoint ENI {dest_eni_id}",
                    duration_ms=int((time.time() - start_time) * 1000)
                )

            dest_owner = eni_details['NetworkInterfaces'][0]['OwnerId']
            dest_eni_arn = f"arn:aws:ec2:{self.region}:{dest_owner}:network-interface/{dest_eni_id}"

            # Create and run path analysis
            analysis_id = self._create_reachability_analysis(
                source_eni_arn, dest_eni_arn, protocol, port
            )

            result = self._wait_for_analysis(analysis_id)
            reachable = result.get('NetworkPathFound', False)

            return TestCase(
                name=f"PrivateLink-{protocol}:{port}",
                result=TestResult.PASS if reachable else TestResult.FAIL,
                message=f"Path {'found' if reachable else 'not found'} to endpoint {endpoint_id}",
                duration_ms=int((time.time() - start_time) * 1000),
                metadata={
                    'analysis_id': analysis_id,
                    'reachable': reachable,
                    'endpoint_enis': len(endpoint_enis),
                    'state': state
                }
            )

        except Exception as e:
            return TestCase(
                name=f"PrivateLink-{protocol}:{port}",
                result=TestResult.FAIL,
                message=f"Test error: {str(e)}",
                duration_ms=int((time.time() - start_time) * 1000)
            )

    def test_connectivity(self,
                          connection_type: ConnectionType,
                          source_vpc: str,
                          dest_vpc: str,
                          connection_id: str,
                          protocol: str = '-1',
                          port: int = None) -> TestCase:
        """
        Unified interface that dispatches to appropriate test method
        based on connection type.
        """
        if connection_type == ConnectionType.TRANSIT_GATEWAY:
            return self.test_tgw_reachability(
                source_vpc, dest_vpc, connection_id, protocol, port
            )

        elif connection_type == ConnectionType.VPC_PEERING:
            return self.test_peering_reachability(
                source_vpc, dest_vpc, connection_id, protocol, port
            )

        elif connection_type == ConnectionType.VPN:
            return self.test_vpn_reachability(
                source_vpc, connection_id, protocol, port
            )

        elif connection_type == ConnectionType.PRIVATELINK:
            return self.test_privatelink_reachability(
                source_vpc, connection_id, protocol, port
            )

        else:
            return TestCase(
                name=f"Unknown-{connection_type.value}",
                result=TestResult.SKIP,
                message=f"Unknown connection type: {connection_type}",
                duration_ms=0
            )

    def _find_tgw_attachment_arn(self, vpc_id: str, tgw_id: str) -> Optional[str]:
        """Find TGW attachment ARN."""
        attachments = self.ec2.describe_transit_gateway_vpc_attachments(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
                {'Name': 'transit-gateway-id', 'Values': [tgw_id]},
                {'Name': 'state', 'Values': ['available']}
            ]
        )

        if not attachments['TransitGatewayVpcAttachments']:
            return None

        att = attachments['TransitGatewayVpcAttachments'][0]
        # TransitGatewayOwnerId only present for RAM-shared TGWs, fall back to VpcOwnerId
        owner_id = att.get('TransitGatewayOwnerId') or att.get('VpcOwnerId')
        return f"arn:aws:ec2:{self.region}:{owner_id}:transit-gateway-attachment/{att['TransitGatewayAttachmentId']}"

    def _find_suitable_eni(self, vpc_id: str) -> Optional[str]:
        """Find a suitable ENI for testing (Lambda, NAT GW, etc.)."""
        enis = self.ec2.describe_network_interfaces(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
                {'Name': 'status', 'Values': ['in-use']}
            ]
        )

        # Prefer Lambda ENIs
        for eni in enis['NetworkInterfaces']:
            description = eni.get('Description', '').lower()
            if 'lambda' in description or 'AWS Lambda' in description:
                eni_id = eni['NetworkInterfaceId']
                return f"arn:aws:ec2:{self.region}:{eni['OwnerId']}:network-interface/{eni_id}"

        # Fallback to any available ENI
        if enis['NetworkInterfaces']:
            eni = enis['NetworkInterfaces'][0]
            eni_id = eni['NetworkInterfaceId']
            return f"arn:aws:ec2:{self.region}:{eni['OwnerId']}:network-interface/{eni_id}"

        return None

    def _find_existing_path(self,
                            source_arn: str,
                            dest_arn: str,
                            protocol: str,
                            port: Optional[int]) -> Optional[str]:
        """
        Find existing Network Insights Path with same parameters.
        Returns path_id if found, None otherwise.
        """
        # Check local cache first
        cache_key = (source_arn, dest_arn, protocol, port)
        if cache_key in self._path_cache:
            # Verify path still exists
            try:
                self.ec2.describe_network_insights_paths(
                    NetworkInsightsPathIds=[self._path_cache[cache_key]]
                )
                return self._path_cache[cache_key]
            except Exception:
                # Path no longer exists, remove from cache
                del self._path_cache[cache_key]

        # Search for existing paths
        try:
            paginator = self.ec2.get_paginator('describe_network_insights_paths')
            for page in paginator.paginate():
                for path in page['NetworkInsightsPaths']:
                    if (path.get('Source') == source_arn and
                        path.get('Destination') == dest_arn and
                        path.get('Protocol') == protocol):

                        # Check port match for tcp/udp
                        path_port = path.get('DestinationPort')
                        if protocol in ['tcp', 'udp']:
                            if path_port == port:
                                path_id = path['NetworkInsightsPathId']
                                self._path_cache[cache_key] = path_id
                                return path_id
                        else:
                            # Protocol doesn't use ports
                            path_id = path['NetworkInsightsPathId']
                            self._path_cache[cache_key] = path_id
                            return path_id
        except Exception:
            pass

        return None

    def _get_or_create_path(self,
                            source_arn: str,
                            dest_arn: str,
                            protocol: str,
                            port: Optional[int]) -> str:
        """
        Get existing path or create new one (idempotent).
        Returns path_id.
        """
        # Try to find existing path
        existing_path = self._find_existing_path(source_arn, dest_arn, protocol, port)
        if existing_path:
            return existing_path

        # Create new path
        params = {
            'Source': source_arn,
            'Destination': dest_arn,
            'Protocol': protocol
        }

        if port and protocol in ['tcp', 'udp']:
            params['DestinationPort'] = port

        path = self.ec2.create_network_insights_path(**params)
        path_id = path['NetworkInsightsPath']['NetworkInsightsPathId']

        # Cache it
        cache_key = (source_arn, dest_arn, protocol, port)
        self._path_cache[cache_key] = path_id

        return path_id

    def _create_reachability_analysis(self,
                                      source_arn: str,
                                      dest_arn: str,
                                      protocol: str,
                                      port: Optional[int]) -> str:
        """Create Network Insights analysis (idempotent path creation)."""
        path_id = self._get_or_create_path(source_arn, dest_arn, protocol, port)

        analysis = self.ec2.start_network_insights_analysis(
            NetworkInsightsPathId=path_id
        )

        return analysis['NetworkInsightsAnalysis']['NetworkInsightsAnalysisId']

    def _wait_for_analysis(self, analysis_id: str, timeout: int = 300) -> Dict:
        """Wait for analysis to complete."""
        start = time.time()
        while time.time() - start < timeout:
            response = self.ec2.describe_network_insights_analyses(
                NetworkInsightsAnalysisIds=[analysis_id]
            )

            analysis = response['NetworkInsightsAnalyses'][0]
            status = analysis['Status']

            if status == 'succeeded':
                return analysis
            elif status == 'failed':
                raise Exception(f"Analysis failed: {analysis.get('StatusMessage')}")

            time.sleep(5)

        raise TimeoutError("Analysis timeout")
