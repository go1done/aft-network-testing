"""
Tests for reachability testing module.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch

from reachability import ReachabilityTester
from models import ConnectionType, TestResult


class TestReachabilityTesterInit:
    """Test ReachabilityTester initialization."""

    def test_init_with_auth_config(self):
        mock_auth = MagicMock()
        tester = ReachabilityTester(auth_config=mock_auth, region="us-west-2")

        assert tester.auth_config == mock_auth
        assert tester.region == "us-west-2"
        assert tester._hub_session is None
        assert tester._ec2 is None

    def test_init_without_auth_config(self):
        tester = ReachabilityTester(region="us-east-1")

        assert tester.auth_config is None
        assert tester.region == "us-east-1"


class TestReachabilityTesterTestConnectivity:
    """Test unified test_connectivity method."""

    def test_test_connectivity_tgw(self):
        tester = ReachabilityTester()
        tester.test_tgw_reachability = MagicMock(return_value=MagicMock(result=TestResult.PASS))

        result = tester.test_connectivity(
            connection_type=ConnectionType.TRANSIT_GATEWAY,
            source_vpc="vpc-source",
            dest_vpc="vpc-dest",
            connection_id="tgw-123",
            protocol="tcp",
            port=443,
            source_account="account-a",
            dest_account="account-b",
        )

        # Verify called with correct args (path_meta dict is passed as last param)
        tester.test_tgw_reachability.assert_called_once()
        call_args = tester.test_tgw_reachability.call_args[0]
        assert call_args[:5] == ("vpc-source", "vpc-dest", "tgw-123", "tcp", 443)
        assert call_args[5]['source_account'] == "account-a"
        assert call_args[5]['dest_account'] == "account-b"

    def test_test_connectivity_peering(self):
        tester = ReachabilityTester()
        tester.test_peering_reachability = MagicMock(return_value=MagicMock(result=TestResult.PASS))

        result = tester.test_connectivity(
            connection_type=ConnectionType.VPC_PEERING,
            source_vpc="vpc-source",
            dest_vpc="vpc-dest",
            connection_id="pcx-123",
        )

        tester.test_peering_reachability.assert_called_once()

    def test_test_connectivity_vpn(self):
        tester = ReachabilityTester()
        tester.test_vpn_reachability = MagicMock(return_value=MagicMock(result=TestResult.PASS))

        result = tester.test_connectivity(
            connection_type=ConnectionType.VPN,
            source_vpc="vpc-source",
            dest_vpc="vpc-dest",
            connection_id="vpn-123",
        )

        tester.test_vpn_reachability.assert_called_once()

    def test_test_connectivity_privatelink(self):
        tester = ReachabilityTester()
        tester.test_privatelink_reachability = MagicMock(return_value=MagicMock(result=TestResult.PASS))

        result = tester.test_connectivity(
            connection_type=ConnectionType.PRIVATELINK,
            source_vpc="vpc-source",
            dest_vpc="vpc-dest",
            connection_id="vpce-123",
        )

        tester.test_privatelink_reachability.assert_called_once()

    def test_test_connectivity_unknown_type(self):
        tester = ReachabilityTester()

        result = tester.test_connectivity(
            connection_type=ConnectionType.DIRECT_CONNECT,  # Not fully implemented
            source_vpc="vpc-source",
            dest_vpc="vpc-dest",
            connection_id="dx-123",
        )

        assert result.result == TestResult.SKIP


class TestReachabilityTesterTGW:
    """Test Transit Gateway reachability testing."""

    def test_test_tgw_reachability_success(self):
        mock_auth = MagicMock()
        mock_session = MagicMock()
        mock_ec2 = MagicMock()

        # Mock TGW attachment lookup
        mock_ec2.describe_transit_gateway_vpc_attachments.return_value = {
            'TransitGatewayVpcAttachments': [{
                'TransitGatewayId': 'tgw-123',
                'TransitGatewayAttachmentId': 'tgw-attach-123',
                'TransitGatewayOwnerId': '111111111111',
                'State': 'available',
            }]
        }

        # Mock path creation and analysis
        mock_ec2.create_network_insights_path.return_value = {
            'NetworkInsightsPath': {'NetworkInsightsPathId': 'nip-123'}
        }
        mock_ec2.start_network_insights_analysis.return_value = {
            'NetworkInsightsAnalysis': {'NetworkInsightsAnalysisId': 'nia-123'}
        }
        mock_ec2.describe_network_insights_analyses.return_value = {
            'NetworkInsightsAnalyses': [{
                'Status': 'succeeded',
                'NetworkPathFound': True,
            }]
        }
        mock_ec2.describe_network_insights_paths.side_effect = Exception("Not found")
        mock_ec2.get_paginator.return_value.paginate.return_value = [{'NetworkInsightsPaths': []}]

        mock_session.client.return_value = mock_ec2
        mock_auth.get_hub_session.return_value = mock_session

        tester = ReachabilityTester(auth_config=mock_auth)
        tester._hub_session = mock_session
        tester._ec2 = mock_ec2

        result = tester.test_tgw_reachability(
            source_vpc="vpc-source",
            dest_vpc="vpc-dest",
            tgw_id="tgw-123",
            protocol="tcp",
            port=443,
        )

        assert result.result == TestResult.PASS
        assert "found" in result.message.lower()

    def test_test_tgw_reachability_attachment_not_found(self):
        mock_ec2 = MagicMock()
        mock_ec2.describe_transit_gateway_vpc_attachments.return_value = {
            'TransitGatewayVpcAttachments': []
        }

        tester = ReachabilityTester()
        tester._ec2 = mock_ec2

        result = tester.test_tgw_reachability(
            source_vpc="vpc-source",
            dest_vpc="vpc-dest",
            tgw_id="tgw-123",
        )

        assert result.result == TestResult.SKIP
        assert "not found" in result.message.lower()


class TestReachabilityTesterVPN:
    """Test VPN connectivity testing."""

    def test_test_vpn_reachability_tunnels_up(self):
        mock_ec2 = MagicMock()
        mock_ec2.describe_vpn_connections.return_value = {
            'VpnConnections': [{
                'VpnConnectionId': 'vpn-123',
                'State': 'available',
                'VgwTelemetry': [
                    {'Status': 'UP'},
                    {'Status': 'UP'},
                ],
            }]
        }

        tester = ReachabilityTester()
        tester._ec2 = mock_ec2

        result = tester.test_vpn_reachability(
            vpc_id="vpc-123",
            vpn_id="vpn-123",
        )

        assert result.result == TestResult.PASS
        assert "2/2 tunnels UP" in result.message

    def test_test_vpn_reachability_tunnels_down(self):
        mock_ec2 = MagicMock()
        mock_ec2.describe_vpn_connections.return_value = {
            'VpnConnections': [{
                'VpnConnectionId': 'vpn-123',
                'State': 'available',
                'VgwTelemetry': [
                    {'Status': 'DOWN'},
                    {'Status': 'DOWN'},
                ],
            }]
        }

        tester = ReachabilityTester()
        tester._ec2 = mock_ec2

        result = tester.test_vpn_reachability(
            vpc_id="vpc-123",
            vpn_id="vpn-123",
        )

        assert result.result == TestResult.WARN
        assert "DOWN" in result.message

    def test_test_vpn_reachability_not_found(self):
        mock_ec2 = MagicMock()
        mock_ec2.describe_vpn_connections.return_value = {
            'VpnConnections': []
        }

        tester = ReachabilityTester()
        tester._ec2 = mock_ec2

        result = tester.test_vpn_reachability(
            vpc_id="vpc-123",
            vpn_id="vpn-notfound",
        )

        assert result.result == TestResult.SKIP


class TestReachabilityTesterPrivateLink:
    """Test PrivateLink connectivity testing."""

    def test_test_privatelink_path_analysis_success(self):
        """Test that PrivateLink does actual path analysis, not just status check."""
        mock_ec2 = MagicMock()
        mock_ec2.describe_vpc_endpoints.return_value = {
            'VpcEndpoints': [{
                'VpcEndpointId': 'vpce-123',
                'VpcId': 'vpc-123',
                'State': 'available',
                'NetworkInterfaceIds': ['eni-endpoint-1', 'eni-endpoint-2'],
            }]
        }
        # Mock finding source ENI
        mock_ec2.describe_network_interfaces.return_value = {
            'NetworkInterfaces': [{
                'NetworkInterfaceId': 'eni-source',
                'OwnerId': '111111111111',
                'Description': 'Lambda ENI',
            }]
        }
        # Mock path creation and analysis
        mock_ec2.get_paginator.return_value.paginate.return_value = [{'NetworkInsightsPaths': []}]
        mock_ec2.create_network_insights_path.return_value = {
            'NetworkInsightsPath': {'NetworkInsightsPathId': 'nip-123'}
        }
        mock_ec2.start_network_insights_analysis.return_value = {
            'NetworkInsightsAnalysis': {'NetworkInsightsAnalysisId': 'nia-123'}
        }
        mock_ec2.describe_network_insights_analyses.return_value = {
            'NetworkInsightsAnalyses': [{
                'Status': 'succeeded',
                'NetworkPathFound': True,
            }]
        }

        tester = ReachabilityTester(region="us-east-1")
        tester._ec2 = mock_ec2

        result = tester.test_privatelink_reachability(
            vpc_id="vpc-123",
            endpoint_id="vpce-123",
            protocol="tcp",
            port=443,
        )

        assert result.result == TestResult.PASS
        assert "path" in result.message.lower() or "found" in result.message.lower()
        # Verify path analysis was called, not just status check
        mock_ec2.start_network_insights_analysis.assert_called_once()

    def test_test_privatelink_path_analysis_blocked(self):
        """Test that PrivateLink detects blocked paths (e.g., security group issue)."""
        mock_ec2 = MagicMock()
        mock_ec2.describe_vpc_endpoints.return_value = {
            'VpcEndpoints': [{
                'VpcEndpointId': 'vpce-123',
                'VpcId': 'vpc-123',
                'State': 'available',
                'NetworkInterfaceIds': ['eni-endpoint-1'],
            }]
        }
        mock_ec2.describe_network_interfaces.return_value = {
            'NetworkInterfaces': [{
                'NetworkInterfaceId': 'eni-source',
                'OwnerId': '111111111111',
                'Description': 'App ENI',
            }]
        }
        mock_ec2.get_paginator.return_value.paginate.return_value = [{'NetworkInsightsPaths': []}]
        mock_ec2.create_network_insights_path.return_value = {
            'NetworkInsightsPath': {'NetworkInsightsPathId': 'nip-123'}
        }
        mock_ec2.start_network_insights_analysis.return_value = {
            'NetworkInsightsAnalysis': {'NetworkInsightsAnalysisId': 'nia-123'}
        }
        mock_ec2.describe_network_insights_analyses.return_value = {
            'NetworkInsightsAnalyses': [{
                'Status': 'succeeded',
                'NetworkPathFound': False,  # Path blocked!
            }]
        }

        tester = ReachabilityTester(region="us-east-1")
        tester._ec2 = mock_ec2

        result = tester.test_privatelink_reachability(
            vpc_id="vpc-123",
            endpoint_id="vpce-123",
            protocol="tcp",
            port=443,
        )

        assert result.result == TestResult.FAIL
        assert "not found" in result.message.lower() or "blocked" in result.message.lower()

    def test_test_privatelink_not_available(self):
        """Test that unavailable endpoint fails fast without path analysis."""
        mock_ec2 = MagicMock()
        mock_ec2.describe_vpc_endpoints.return_value = {
            'VpcEndpoints': [{
                'VpcEndpointId': 'vpce-123',
                'State': 'pending',
                'NetworkInterfaceIds': [],
            }]
        }

        tester = ReachabilityTester()
        tester._ec2 = mock_ec2

        result = tester.test_privatelink_reachability(
            vpc_id="vpc-123",
            endpoint_id="vpce-123",
        )

        assert result.result == TestResult.FAIL
        # Should NOT attempt path analysis if endpoint is not available
        mock_ec2.start_network_insights_analysis.assert_not_called()

    def test_test_privatelink_no_source_eni(self):
        """Test graceful handling when no source ENI is found."""
        mock_ec2 = MagicMock()
        mock_ec2.describe_vpc_endpoints.return_value = {
            'VpcEndpoints': [{
                'VpcEndpointId': 'vpce-123',
                'VpcId': 'vpc-123',
                'State': 'available',
                'NetworkInterfaceIds': ['eni-endpoint-1'],
            }]
        }
        mock_ec2.describe_network_interfaces.return_value = {
            'NetworkInterfaces': []  # No ENIs in VPC
        }

        tester = ReachabilityTester()
        tester._ec2 = mock_ec2

        result = tester.test_privatelink_reachability(
            vpc_id="vpc-123",
            endpoint_id="vpce-123",
        )

        assert result.result == TestResult.WARN
        assert "eni" in result.message.lower() or "source" in result.message.lower()

    def test_test_privatelink_not_found(self):
        mock_ec2 = MagicMock()
        mock_ec2.describe_vpc_endpoints.return_value = {
            'VpcEndpoints': []
        }

        tester = ReachabilityTester()
        tester._ec2 = mock_ec2

        result = tester.test_privatelink_reachability(
            vpc_id="vpc-123",
            endpoint_id="vpce-notfound",
        )

        assert result.result == TestResult.SKIP


class TestReachabilityTesterPeering:
    """Test VPC Peering reachability testing."""

    def test_test_peering_active(self):
        mock_ec2 = MagicMock()
        mock_ec2.describe_vpc_peering_connections.return_value = {
            'VpcPeeringConnections': [{
                'VpcPeeringConnectionId': 'pcx-123',
                'Status': {'Code': 'active'},
            }]
        }
        mock_ec2.describe_network_interfaces.return_value = {
            'NetworkInterfaces': []  # No ENIs for full test
        }

        tester = ReachabilityTester()
        tester._ec2 = mock_ec2

        result = tester.test_peering_reachability(
            source_vpc="vpc-source",
            dest_vpc="vpc-dest",
            peering_id="pcx-123",
        )

        # Should warn because no ENIs available for testing
        assert result.result == TestResult.WARN
        assert "active" in result.message.lower()

    def test_test_peering_not_active(self):
        mock_ec2 = MagicMock()
        mock_ec2.describe_vpc_peering_connections.return_value = {
            'VpcPeeringConnections': [{
                'VpcPeeringConnectionId': 'pcx-123',
                'Status': {'Code': 'pending-acceptance'},
            }]
        }

        tester = ReachabilityTester()
        tester._ec2 = mock_ec2

        result = tester.test_peering_reachability(
            source_vpc="vpc-source",
            dest_vpc="vpc-dest",
            peering_id="pcx-123",
        )

        assert result.result == TestResult.FAIL


class TestReachabilityTesterPathCaching:
    """Test Network Insights Path caching."""

    def test_get_or_create_path_creates_new(self):
        mock_ec2 = MagicMock()
        mock_ec2.get_paginator.return_value.paginate.return_value = [{'NetworkInsightsPaths': []}]
        mock_ec2.create_network_insights_path.return_value = {
            'NetworkInsightsPath': {'NetworkInsightsPathId': 'nip-new'}
        }

        tester = ReachabilityTester()
        tester._ec2 = mock_ec2

        path_id = tester._get_or_create_path(
            source_arn="arn:aws:ec2:us-east-1:111:tgw-attach/source",
            dest_arn="arn:aws:ec2:us-east-1:111:tgw-attach/dest",
            protocol="tcp",
            port=443,
        )

        assert path_id == "nip-new"
        mock_ec2.create_network_insights_path.assert_called_once()

    def test_get_or_create_path_uses_cached(self):
        mock_ec2 = MagicMock()

        tester = ReachabilityTester()
        tester._ec2 = mock_ec2
        # Pre-populate cache
        cache_key = ("arn:source", "arn:dest", "tcp", 443)
        tester._path_cache[cache_key] = "nip-cached"
        mock_ec2.describe_network_insights_paths.return_value = {
            'NetworkInsightsPaths': [{'NetworkInsightsPathId': 'nip-cached'}]
        }

        path_id = tester._get_or_create_path(
            source_arn="arn:source",
            dest_arn="arn:dest",
            protocol="tcp",
            port=443,
        )

        assert path_id == "nip-cached"
        mock_ec2.create_network_insights_path.assert_not_called()
