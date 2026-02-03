"""
Data Models and Enums
Shared across all modules
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

# =============================================================================
# ENUMS
# =============================================================================

class ExecutionMode(Enum):
    """Execution environment"""
    LOCAL = "local"
    AWS_LAMBDA = "aws"
    AWS_CODEBUILD = "codebuild"

class TestPhase(Enum):
    """Test execution phase"""
    PRE_RELEASE = "pre-release"
    POST_RELEASE = "post-release"

class TestResult(Enum):
    """Test result status"""
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"

class ConnectionType(Enum):
    """Network connection types"""
    TRANSIT_GATEWAY = "tgw"
    VPC_PEERING = "pcx"
    VPN = "vpn"
    DIRECT_CONNECT = "dx"
    PRIVATELINK = "vpce"

# =============================================================================
# CONFIGURATION MODELS
# =============================================================================

@dataclass
class AccountConfig:
    """Account configuration - minimal input required"""
    account_id: str
    account_name: str
    # vpc_id is auto-discovered if not provided
    vpc_id: Optional[str] = None
    region: str = "us-west-2"
    tgw_id: Optional[str] = None
    expected_routes: List[str] = field(default_factory=list)
    test_ports: List[int] = field(default_factory=list)

# =============================================================================
# BASELINE MODELS
# =============================================================================


@dataclass
class VPCBaseline:
    """VPC configuration baseline"""
    vpc_id: str
    cidr_block: str
    dns_support: bool
    dns_hostnames: bool
    subnet_count: int
    subnet_cidrs: List[str]
    availability_zones: List[str]
    cidr_block_associations: Optional[List[str]] = None  # Secondary CIDRs

@dataclass
class SecurityGroupRule:
    """Security group rule"""
    protocol: str
    from_port: int
    to_port: int
    cidr_blocks: List[str]
    description: str = ""


@dataclass
class RouteTableBaseline:
    """Route table configuration baseline"""
    route_table_id: str
    main: bool
    routes: List[Dict]
    associated_subnets: List[str]


@dataclass
class SecurityGroupBaseline:
    """Security group configuration baseline"""
    group_id: str
    group_name: str
    ingress_rules: List[Dict]
    egress_rules: List[Dict]


@dataclass
class NetworkACLBaseline:
    """Network ACL configuration baseline"""
    nacl_id: str
    ingress_rules: List[Dict]
    egress_rules: List[Dict]
    associated_subnets: List[str]


@dataclass
class TransitGatewayBaseline:
    """Transit Gateway attachment baseline"""
    tgw_id: str
    tgw_attachment_id: str
    attachment_state: str
    subnet_ids: List[str]
    route_table_id: Optional[str]
    appliance_mode: bool = False

@dataclass
class AccountNetworkBaseline:
    """Complete network baseline for an account"""
    account_id: str
    account_name: str
    region: str
    vpc: VPCBaseline
    transit_gateway: Optional[TransitGatewayBaseline]
    allowed_ports: List[SecurityGroupRule]
    discovered_at: str
    # Extended fields (optional for backward compatibility)
    route_tables: Optional[List[RouteTableBaseline]] = None
    security_groups: Optional[List[SecurityGroupBaseline]] = None
    network_acls: Optional[List[NetworkACLBaseline]] = None
    tags: Optional[Dict[str, str]] = None

# =============================================================================
# CONNECTIVITY MODELS
# =============================================================================

@dataclass
class VPCConnectivityPattern:
    """Discovered VPC-to-VPC connectivity"""
    source_vpc_id: str
    source_account_id: str
    source_account_name: str
    dest_vpc_id: str
    dest_account_id: str
    dest_account_name: str
    connection_type: ConnectionType
    connection_id: str
    expected: bool
    traffic_observed: bool
    protocols_observed: Set[str] = field(default_factory=set)
    ports_observed: Set[int] = field(default_factory=set)
    ports_allowed: Set[int] = field(default_factory=set)  # From security groups/NACLs
    first_seen: str = ""
    last_seen: str = ""
    packet_count: int = 0
    use_case: str = "general"

@dataclass
class TGWTopology:
    """Transit Gateway topology"""
    tgw_id: str
    tgw_name: str
    owner_account: str
    route_tables: List[Dict]
    attachments: List[Dict]
    vpc_connectivity_matrix: Dict[str, List[str]]

# =============================================================================
# TEST MODELS
# =============================================================================

@dataclass
class TestCase:
    """Individual test case result"""
    name: str
    result: TestResult
    message: str
    duration_ms: int
    metadata: Optional[Dict] = None

@dataclass
class TestSummary:
    """Test suite summary"""
    phase: str
    start_time: str
    end_time: str
    duration_seconds: float
    total_tests: int
    passed: int
    failed: int
    warnings: int
    skipped: int
    results: List[Dict]