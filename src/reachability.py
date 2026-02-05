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
from botocore.exceptions import ClientError

from models import ConnectionType, TestResult, TestCase


# Retryable error codes
RETRYABLE_ERRORS = [
    'RequestExpired',
    'ExpiredTokenException',
    'ExpiredToken',
    'Throttling',
    'RequestLimitExceeded',
    'ServiceUnavailable',
]


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

    def _refresh_ec2_client(self):
        """Refresh EC2 client with new credentials."""
        # Clear cached session and client
        if self.auth_config:
            self.auth_config.clear_session_cache()
        self._hub_session = None
        self._ec2 = None
        # Force re-initialization
        _ = self.ec2

    def _retry_on_error(self, func, *args, max_retries: int = 5, **kwargs):
        """
        Retry function on throttling or expired credentials.

        Prioritizes throttling handling with longer backoff.

        Args:
            func: Function to call
            max_retries: Maximum retry attempts
            *args, **kwargs: Arguments to pass to function

        Returns:
            Function result

        Raises:
            Last exception if all retries fail
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                last_error = e

                if error_code in ['Throttling', 'RequestLimitExceeded', 'TooManyRequestsException']:
                    # Throttling: longer exponential backoff (5, 10, 20, 40, 80 seconds)
                    wait_time = 5 * (2 ** attempt)
                    print(f"  ⚠️  API throttled, waiting {wait_time}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(wait_time)
                elif error_code in ['RequestExpired', 'ExpiredTokenException', 'ExpiredToken']:
                    # Credential expiry: refresh and retry quickly
                    wait_time = 2
                    print(f"  ⚠️  Credentials expired, refreshing...")
                    time.sleep(wait_time)
                    self._refresh_ec2_client()
                elif error_code == 'ServiceUnavailable':
                    wait_time = 10 * (attempt + 1)
                    print(f"  ⚠️  Service unavailable, waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise
            except Exception as e:
                error_str = str(e).lower()
                if 'expired' in error_str or 'expiredtoken' in error_str:
                    last_error = e
                    print(f"  ⚠️  Token expired, refreshing credentials...")
                    time.sleep(2)
                    self._refresh_ec2_client()
                elif 'throttl' in error_str or 'rate' in error_str:
                    last_error = e
                    wait_time = 5 * (2 ** attempt)
                    print(f"  ⚠️  Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise

        raise last_error

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
                              port: int = None,
                              path_meta: Dict = None) -> TestCase:
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
                source_arn, dest_arn, protocol, port, path_meta
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
                                  port: int = None,
                                  path_meta: Dict = None) -> TestCase:
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
                source_eni, dest_eni, protocol, port, path_meta
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
                              port: int = None,
                              path_meta: Dict = None) -> TestCase:
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
                                      port: int = 443,
                                      path_meta: Dict = None) -> TestCase:
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
            path_meta: Metadata for NRA path naming

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
                source_eni_arn, dest_eni_arn, protocol, port, path_meta
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
                          port: int = None,
                          source_account: str = None,
                          dest_account: str = None) -> TestCase:
        """
        Unified interface that dispatches to appropriate test method
        based on connection type.

        Args:
            connection_type: Type of connection (TGW, peering, VPN, PrivateLink)
            source_vpc: Source VPC ID
            dest_vpc: Destination VPC ID
            connection_id: Connection resource ID (tgw-xxx, pcx-xxx, etc.)
            protocol: Protocol to test ('-1' for all, 'tcp', 'udp')
            port: Port number (optional)
            source_account: Source account name (for NRA path naming)
            dest_account: Destination account name (for NRA path naming)
        """
        # Build path metadata for naming in NRA
        path_meta = {
            'source_account': source_account or 'unknown',
            'dest_account': dest_account or 'unknown',
            'connection_type': connection_type.value,
            'connection_id': connection_id,
        }

        if connection_type == ConnectionType.TRANSIT_GATEWAY:
            return self.test_tgw_reachability(
                source_vpc, dest_vpc, connection_id, protocol, port, path_meta
            )

        elif connection_type == ConnectionType.VPC_PEERING:
            return self.test_peering_reachability(
                source_vpc, dest_vpc, connection_id, protocol, port, path_meta
            )

        elif connection_type == ConnectionType.VPN:
            return self.test_vpn_reachability(
                source_vpc, connection_id, protocol, port, path_meta
            )

        elif connection_type == ConnectionType.PRIVATELINK:
            return self.test_privatelink_reachability(
                source_vpc, connection_id, protocol, port, path_meta
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
                            port: Optional[int],
                            path_meta: Dict = None) -> str:
        """
        Get existing path or create new one (idempotent).
        Returns path_id.
        """
        # Try to find existing path
        existing_path = self._find_existing_path(source_arn, dest_arn, protocol, port)
        if existing_path:
            return existing_path

        # Build descriptive name for NRA console
        if path_meta:
            src = path_meta.get('source_account', 'unknown')
            dst = path_meta.get('dest_account', 'unknown')
            conn_type = path_meta.get('connection_type', 'unknown')
            port_str = str(port) if port else 'all'
            path_name = f"aft: {src} -> {dst} ({conn_type}) {protocol}:{port_str}"
        else:
            path_name = f"aft-network-test-{protocol}-{port or 'all'}"

        # Build tags
        tags = [
            {'Key': 'Name', 'Value': path_name[:255]},  # AWS tag limit
            {'Key': 'CreatedBy', 'Value': 'aft-network-testing'},
        ]
        if path_meta:
            tags.extend([
                {'Key': 'SourceAccount', 'Value': path_meta.get('source_account', 'unknown')[:255]},
                {'Key': 'DestAccount', 'Value': path_meta.get('dest_account', 'unknown')[:255]},
                {'Key': 'ConnectionType', 'Value': path_meta.get('connection_type', 'unknown')},
                {'Key': 'ConnectionId', 'Value': path_meta.get('connection_id', 'unknown')[:255]},
            ])

        # Create new path
        params = {
            'Source': source_arn,
            'Destination': dest_arn,
            'Protocol': protocol,
            'TagSpecifications': [{
                'ResourceType': 'network-insights-path',
                'Tags': tags
            }]
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
                                      port: Optional[int],
                                      path_meta: Dict = None) -> str:
        """Create Network Insights analysis (idempotent path creation)."""
        path_id = self._get_or_create_path(source_arn, dest_arn, protocol, port, path_meta)

        def start_analysis():
            return self.ec2.start_network_insights_analysis(
                NetworkInsightsPathId=path_id
            )

        analysis = self._retry_on_error(start_analysis)
        return analysis['NetworkInsightsAnalysis']['NetworkInsightsAnalysisId']

    def _wait_for_analysis(self, analysis_id: str, timeout: int = 300) -> Dict:
        """Wait for analysis to complete with retry on expired credentials."""
        start = time.time()
        while time.time() - start < timeout:
            def describe_analysis():
                return self.ec2.describe_network_insights_analyses(
                    NetworkInsightsAnalysisIds=[analysis_id]
                )

            response = self._retry_on_error(describe_analysis)

            analysis = response['NetworkInsightsAnalyses'][0]
            status = analysis['Status']

            if status == 'succeeded':
                return analysis
            elif status == 'failed':
                raise Exception(f"Analysis failed: {analysis.get('StatusMessage')}")

            time.sleep(5)

        raise TimeoutError("Analysis timeout")
