"""
Tests for data models and enums.
"""

import pytest
from dataclasses import asdict

from models import (
    ExecutionMode,
    TestPhase,
    TestResult,
    ConnectionType,
    AccountConfig,
    VPCBaseline,
    TransitGatewayBaseline,
    RouteTableBaseline,
    SecurityGroupBaseline,
    NetworkACLBaseline,
    SecurityGroupRule,
    VPCConnectivityPattern,
    TGWTopology,
    TestCase,
    TestSummary,
)


class TestEnums:
    """Test enum definitions."""

    def test_execution_mode_values(self):
        assert ExecutionMode.LOCAL.value == "local"
        assert ExecutionMode.AWS_LAMBDA.value == "aws"
        assert ExecutionMode.AWS_CODEBUILD.value == "codebuild"

    def test_test_phase_values(self):
        assert TestPhase.PRE_RELEASE.value == "pre-release"
        assert TestPhase.POST_RELEASE.value == "post-release"
        # pre-flight was removed
        assert len(TestPhase) == 2

    def test_test_result_values(self):
        assert TestResult.PASS.value == "PASS"
        assert TestResult.FAIL.value == "FAIL"
        assert TestResult.WARN.value == "WARN"
        assert TestResult.SKIP.value == "SKIP"

    def test_connection_type_values(self):
        assert ConnectionType.TRANSIT_GATEWAY.value == "tgw"
        assert ConnectionType.VPC_PEERING.value == "pcx"
        assert ConnectionType.VPN.value == "vpn"
        assert ConnectionType.DIRECT_CONNECT.value == "dx"
        assert ConnectionType.PRIVATELINK.value == "vpce"


class TestAccountConfig:
    """Test AccountConfig dataclass."""

    def test_create_with_all_fields(self):
        config = AccountConfig(
            account_id="123456789012",
            account_name="test-account",
            vpc_id="vpc-abc123",
            region="us-west-2",
            tgw_id="tgw-xyz789",
            expected_routes=["10.0.0.0/8"],
            test_ports=[443, 22],
        )
        assert config.account_id == "123456789012"
        assert config.account_name == "test-account"
        assert config.vpc_id == "vpc-abc123"
        assert config.region == "us-west-2"
        assert config.tgw_id == "tgw-xyz789"
        assert config.expected_routes == ["10.0.0.0/8"]
        assert config.test_ports == [443, 22]

    def test_create_with_defaults(self):
        config = AccountConfig(
            account_id="123456789012",
            account_name="test-account",
        )
        assert config.vpc_id is None
        assert config.region == "us-west-2"
        assert config.tgw_id is None
        assert config.expected_routes == []
        assert config.test_ports == []

    def test_asdict(self):
        config = AccountConfig(
            account_id="123456789012",
            account_name="test-account",
        )
        d = asdict(config)
        assert d["account_id"] == "123456789012"
        assert d["account_name"] == "test-account"
        assert "vpc_id" in d


class TestVPCBaseline:
    """Test VPCBaseline dataclass."""

    def test_create_vpc_baseline(self, sample_vpc_baseline):
        assert sample_vpc_baseline.vpc_id == "vpc-abc123"
        assert sample_vpc_baseline.cidr_block == "10.0.0.0/16"
        assert sample_vpc_baseline.dns_support is True
        assert sample_vpc_baseline.dns_hostnames is True
        assert sample_vpc_baseline.subnet_count == 4
        assert len(sample_vpc_baseline.subnet_cidrs) == 4
        assert len(sample_vpc_baseline.availability_zones) == 2

    def test_vpc_baseline_with_secondary_cidrs(self):
        baseline = VPCBaseline(
            vpc_id="vpc-test",
            cidr_block="10.0.0.0/16",
            dns_support=True,
            dns_hostnames=True,
            subnet_count=2,
            subnet_cidrs=["10.0.1.0/24", "10.0.2.0/24"],
            availability_zones=["us-east-1a"],
            cidr_block_associations=["10.0.0.0/16", "10.1.0.0/16"],
        )
        assert baseline.cidr_block_associations == ["10.0.0.0/16", "10.1.0.0/16"]


class TestTransitGatewayBaseline:
    """Test TransitGatewayBaseline dataclass."""

    def test_create_tgw_baseline(self, sample_tgw_baseline):
        assert sample_tgw_baseline.tgw_id == "tgw-xyz789"
        assert sample_tgw_baseline.attachment_state == "available"
        assert sample_tgw_baseline.appliance_mode is False

    def test_tgw_baseline_with_appliance_mode(self):
        baseline = TransitGatewayBaseline(
            tgw_id="tgw-test",
            tgw_attachment_id="tgw-attach-test",
            attachment_state="available",
            subnet_ids=["subnet-1"],
            route_table_id="tgw-rtb-test",
            appliance_mode=True,
        )
        assert baseline.appliance_mode is True


class TestVPCConnectivityPattern:
    """Test VPCConnectivityPattern dataclass."""

    def test_create_connectivity_pattern(self, sample_connectivity_pattern):
        pattern = sample_connectivity_pattern
        assert pattern.source_vpc_id == "vpc-source"
        assert pattern.dest_vpc_id == "vpc-dest"
        assert pattern.connection_type == ConnectionType.TRANSIT_GATEWAY
        assert pattern.expected is True
        assert pattern.traffic_observed is True
        assert 443 in pattern.ports_observed
        assert "tcp" in pattern.protocols_observed

    def test_connectivity_pattern_defaults(self):
        pattern = VPCConnectivityPattern(
            source_vpc_id="vpc-a",
            source_account_id="111",
            source_account_name="source",
            dest_vpc_id="vpc-b",
            dest_account_id="222",
            dest_account_name="dest",
            connection_type=ConnectionType.VPC_PEERING,
            connection_id="pcx-123",
            expected=True,
            traffic_observed=False,
        )
        assert pattern.protocols_observed == set()
        assert pattern.ports_observed == set()
        assert pattern.first_seen == ""
        assert pattern.last_seen == ""
        assert pattern.packet_count == 0
        assert pattern.use_case == "general"


class TestTestCase:
    """Test TestCase dataclass."""

    def test_create_test_case_pass(self, sample_test_case_pass):
        tc = sample_test_case_pass
        assert tc.name == "TGW-tcp:443"
        assert tc.result == TestResult.PASS
        assert tc.message == "Path found"
        assert tc.duration_ms == 1500
        assert tc.metadata["reachable"] is True

    def test_create_test_case_fail(self, sample_test_case_fail):
        tc = sample_test_case_fail
        assert tc.result == TestResult.FAIL
        assert tc.metadata["reachable"] is False

    def test_test_case_without_metadata(self):
        tc = TestCase(
            name="test",
            result=TestResult.SKIP,
            message="skipped",
            duration_ms=0,
        )
        assert tc.metadata is None


class TestTestSummary:
    """Test TestSummary dataclass."""

    def test_create_test_summary(self):
        summary = TestSummary(
            phase="post-release",
            start_time="2024-01-01T10:00:00",
            end_time="2024-01-01T10:05:00",
            duration_seconds=300.0,
            total_tests=10,
            passed=8,
            failed=1,
            warnings=1,
            skipped=0,
            results=[],
        )
        assert summary.phase == "post-release"
        assert summary.total_tests == 10
        assert summary.passed == 8
        assert summary.failed == 1
