"""
Shared test fixtures for AFT Network Testing Framework.
"""

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import (
    ExecutionMode,
    TestPhase,
    TestResult,
    ConnectionType,
    AccountConfig,
    VPCBaseline,
    TransitGatewayBaseline,
    VPCConnectivityPattern,
    TestCase,
)


@pytest.fixture
def sample_accounts():
    """Sample account configurations for testing."""
    return [
        AccountConfig(
            account_id="111111111111",
            account_name="network-hub",
            vpc_id="vpc-hub123",
            region="us-east-1"
        ),
        AccountConfig(
            account_id="222222222222",
            account_name="prod-app",
            vpc_id="vpc-prod456",
            region="us-east-1"
        ),
        AccountConfig(
            account_id="333333333333",
            account_name="dev-app",
            vpc_id="vpc-dev789",
            region="us-east-1"
        ),
    ]


@pytest.fixture
def sample_accounts_dict():
    """Sample accounts as dictionaries."""
    return [
        {
            "account_id": "111111111111",
            "account_name": "network-hub",
            "vpc_id": "vpc-hub123",
        },
        {
            "account_id": "222222222222",
            "account_name": "prod-app",
            "vpc_id": "vpc-prod456",
        },
    ]


@pytest.fixture
def sample_vpc_baseline():
    """Sample VPC baseline for testing."""
    return VPCBaseline(
        vpc_id="vpc-abc123",
        cidr_block="10.0.0.0/16",
        dns_support=True,
        dns_hostnames=True,
        subnet_count=4,
        subnet_cidrs=["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24", "10.0.4.0/24"],
        availability_zones=["us-east-1a", "us-east-1b"],
    )


@pytest.fixture
def sample_tgw_baseline():
    """Sample Transit Gateway baseline for testing."""
    return TransitGatewayBaseline(
        tgw_id="tgw-xyz789",
        tgw_attachment_id="tgw-attach-abc123",
        attachment_state="available",
        subnet_ids=["subnet-1", "subnet-2"],
        route_table_id="tgw-rtb-123",
        appliance_mode=False,
    )


@pytest.fixture
def sample_connectivity_pattern():
    """Sample connectivity pattern for testing."""
    return VPCConnectivityPattern(
        source_vpc_id="vpc-source",
        source_account_id="111111111111",
        source_account_name="source-account",
        dest_vpc_id="vpc-dest",
        dest_account_id="222222222222",
        dest_account_name="dest-account",
        connection_type=ConnectionType.TRANSIT_GATEWAY,
        connection_id="tgw-xyz789",
        expected=True,
        traffic_observed=True,
        protocols_observed={"tcp", "udp"},
        ports_observed={443, 22, 3306},
        use_case="general",
    )


@pytest.fixture
def sample_golden_path(sample_connectivity_pattern):
    """Sample golden path for testing."""
    return {
        "version": "1.0",
        "generated_at": "2024-01-01T00:00:00",
        "based_on_accounts": 2,
        "expected_configuration": {
            "vpc": {
                "dns_support": True,
                "dns_hostnames": True,
                "min_subnets": 2,
                "min_availability_zones": 2,
            },
            "transit_gateway": {
                "required": True,
                "expected_state": "available",
            },
            "security": {
                "common_ingress_patterns": ["tcp:443", "tcp:22"],
            },
        },
        "connectivity": {
            "tgw_id": "tgw-xyz789",
            "total_paths": 2,
            "active_paths": 1,
            "by_connection_type": {
                "tgw": 2,
                "peering": 0,
                "vpn": 0,
                "privatelink": 0,
            },
            "patterns": [
                {
                    "source_vpc_id": "vpc-hub123",
                    "source_account_id": "111111111111",
                    "source_account_name": "network-hub",
                    "dest_vpc_id": "vpc-prod456",
                    "dest_account_id": "222222222222",
                    "dest_account_name": "prod-app",
                    "connection_type": "tgw",
                    "connection_id": "tgw-xyz789",
                    "expected_reachable": True,
                    "traffic_observed": True,
                    "protocols_observed": ["tcp"],
                    "ports_observed": [443, 22],
                    "ports_allowed": [443, 22, 80, 8080],  # From security groups
                    "use_case": "general",
                },
            ],
        },
        "account_baselines": [],
    }


@pytest.fixture
def sample_test_case_pass():
    """Sample passing test case."""
    return TestCase(
        name="TGW-tcp:443",
        result=TestResult.PASS,
        message="Path found",
        duration_ms=1500,
        metadata={"analysis_id": "nia-123", "reachable": True},
    )


@pytest.fixture
def sample_test_case_fail():
    """Sample failing test case."""
    return TestCase(
        name="TGW-tcp:3306",
        result=TestResult.FAIL,
        message="Path not found",
        duration_ms=2000,
        metadata={"analysis_id": "nia-456", "reachable": False},
    )


@pytest.fixture
def sample_test_summary():
    """Sample test summary."""
    return {
        "phase": "post-release",
        "start_time": "2024-01-01T10:00:00",
        "end_time": "2024-01-01T10:05:00",
        "duration_seconds": 300.0,
        "total_tests": 10,
        "passed": 8,
        "failed": 1,
        "warnings": 1,
        "skipped": 0,
        "results": [],
    }
