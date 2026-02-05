# AFT Network Testing Framework - Design Document

## Overview

Automated network connectivity testing for AWS Control Tower AFT environments. Validates multi-account, multi-region VPC connectivity using AWS Reachability Analyzer and connection-specific APIs.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLI (cli.py)                                   │
│  Entry point: argument parsing, mode selection, phase dispatch              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Orchestrator (orchestrator.py)                       │
│  Coordinates: discovery → testing → reporting                               │
│  Manages: golden path lifecycle, test execution flow                        │
└─────────────────────────────────────────────────────────────────────────────┘
          │                    │                    │
          ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│    Discovery    │  │     Testing     │  │    Reporting    │
│  baseline.py    │  │ reachability.py │  │  reporting.py   │
│ connectivity.py │  │                 │  │                 │
└─────────────────┘  └─────────────────┘  └─────────────────┘
          │                    │                    │
          └────────────────────┴────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Auth (auth.py)                                    │
│  Cross-cutting: session management, role assumption, caching                │
└─────────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Models (models.py)                                 │
│  Shared: dataclasses, enums, type definitions                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. CLI (`cli.py`)

Entry point handling command-line arguments and dispatching to orchestrator.

**Key Arguments:**
| Argument | Description | Default |
|----------|-------------|---------|
| `--mode` | Execution mode: `local`, `aws`, `codebuild` | `local` |
| `--profile` | AWS CLI profile (hub account, local mode) | - |
| `--profile-pattern` | Per-account profile pattern, e.g., `{account_id}` | - |
| `--phase` | Test phase: `discover`, `pre-release`, `post-release` | required |
| `--accounts-file` | YAML file with account configs | `config/accounts.yaml` |
| `--golden-path` | Golden path YAML file | `./golden_path.yaml` |
| `--tgw-id` | Transit Gateway ID (optional, auto-discovered) | - |
| `--connection-types` | Types to discover: `all` or comma-separated | `all` |
| `--publish-results` | Publish to CloudWatch/S3 | `false` |
| `--s3-bucket` | S3 bucket for results | - |

### 2. Orchestrator (`orchestrator.py`)

Coordinates the test lifecycle.

**Methods:**
- `discover_baseline()` - Phase 0: discover VPCs, connectivity, generate golden path
- `run_tests()` - Execute tests against golden path, optionally publish results
- `generate_test_matrix()` - Build test cases from golden path

**Flow:**
```
discover_baseline()
    ├── BaselineDiscovery.scan_all_accounts()
    ├── ConnectivityDiscovery.build_connectivity_map()
    └── Save golden_path.yaml

run_tests()
    ├── Load golden path patterns
    ├── For each pattern: ReachabilityTester.test_connectivity()
    ├── Aggregate results
    └── If publish=True: publish_results()
```

### 3. Authentication (`auth.py`)

Dual-mode authentication with session caching.

**Modes:**
| Mode | Profile Source | Target Account Access |
|------|---------------|----------------------|
| `LOCAL` (single profile) | `--profile` | Same SSO role spans all accounts |
| `LOCAL` (profile pattern) | `--profile-pattern` | Per-account profiles in credentials |
| `AWS` | Instance/Lambda role | STS AssumeRole to `AWSAFTExecution` |

**Session Caching:**
- Sessions cached by account ID
- 50-minute expiry (before 1-hour STS limit)
- `clear_session_cache()` for testing

### 4. Discovery

#### Baseline Discovery (`baseline.py`)

Scans VPC configurations per account:
- VPC CIDR, DNS settings
- Subnets, route tables, NACLs
- Security groups, TGW attachments

#### Connectivity Discovery (`connectivity.py`)

Discovers connection types across accounts:

| Type | Discovery Method | Data Source |
|------|-----------------|-------------|
| Transit Gateway | TGW attachments, route tables | `ec2:DescribeTransitGateway*` |
| VPC Peering | Peering connections | `ec2:DescribeVpcPeeringConnections` |
| VPN | VPN connections, tunnels | `ec2:DescribeVpnConnections` |
| PrivateLink | VPC endpoints | `ec2:DescribeVpcEndpoints` |

**Output:** `VPCConnectivityPattern` objects with:
- Source/dest VPC and account info
- Connection type and ID
- Traffic observations (from VPC Flow Logs if enabled)
- Discovered ports and protocols

### 5. Testing (`reachability.py`)

Unified `test_connectivity()` method dispatches by connection type:

| Connection Type | Test Method | What It Validates |
|-----------------|-------------|-------------------|
| Transit Gateway | Reachability Analyzer | Full path (routes, SGs, NACLs) |
| VPC Peering | Reachability Analyzer | Full path via ENIs |
| VPN | API status check | Tunnel UP/DOWN only |
| PrivateLink | API status check | Endpoint state, ENI health |

**Reachability Analyzer Limitations:**
- Static path analysis only (no actual traffic)
- Single-region only
- Cannot test VPN tunnels to on-prem
- Cannot resolve DNS

### 6. Reporting (`reporting.py`)

**CloudWatch Metrics:** (namespace: `AFT/VPCTests`)
- `TestsPassed`, `TestsFailed`, `TestsWarnings`, `TestsSkipped`
- `TestDuration`, `TotalTests`
- Dimensions: `Phase`

**S3 Results:** (if `--s3-bucket` provided)
- Path: `s3://{bucket}/vpc-tests/{phase}/{timestamp}.json`
- Full test summary JSON

## Data Models (`models.py`)

### Enums

```python
ExecutionMode: LOCAL | AWS_LAMBDA | AWS_CODEBUILD
TestPhase: PRE_RELEASE | POST_RELEASE
TestResult: PASS | FAIL | WARN | SKIP
ConnectionType: TRANSIT_GATEWAY | VPC_PEERING | VPN | DIRECT_CONNECT | PRIVATELINK
```

### Key Dataclasses

```python
AccountConfig          # Input: account_id, account_name, vpc_id (optional), region
VPCBaseline            # Discovered VPC config
VPCConnectivityPattern # Discovered connectivity with traffic data
TestCase               # Individual test result
TestSummary            # Aggregated test results
```

## Golden Path Schema

```yaml
# Auto-generated by discover phase
accounts:
  - account_id: "111111111111"
    account_name: "network-hub"
    vpc:
      vpc_id: "vpc-abc123"
      cidr_block: "10.0.0.0/16"
      # ... full VPC baseline

connectivity:
  tgw_id: "tgw-xyz789"  # May be null if auto-discovered
  total_paths: 10
  active_paths: 8       # Paths with observed traffic
  by_connection_type:
    tgw: 5
    peering: 2
    vpn: 1
    privatelink: 2

  patterns:
    - source_vpc_id: "vpc-abc123"
      source_account_id: "111111111111"
      source_account_name: "network-hub"
      dest_vpc_id: "vpc-def456"
      dest_account_id: "222222222222"
      dest_account_name: "prod-app"
      connection_type: "tgw"
      connection_id: "tgw-attach-xxx"
      expected_reachable: true
      traffic_observed: true
      protocols_observed: ["tcp", "udp"]
      ports_observed: [443, 22, 3306]
      use_case: "general"
```

## Test Phases

| Phase | When | What It Does |
|-------|------|--------------|
| `discover` | Initial setup, after infra changes | Generate/update golden path |
| `pre-release` | Before Terraform apply | Validate current state matches golden path |
| `post-release` | After Terraform apply | Verify connectivity restored |

## Execution Modes

### Local Mode

```bash
# Single SSO profile (role has cross-account access)
aft-test --mode local --profile aft-admin --phase discover ...

# Per-account profiles (credentials file with account IDs)
aft-test --mode local --profile-pattern "{account_id}" --phase discover ...
```

### AWS Mode (Lambda/CodeBuild)

```bash
# Uses instance role, assumes AWSAFTExecution in target accounts
aft-test --mode aws --phase post-release ...
```

## Future Enhancements

### Planned Features

1. **Parallel Test Execution**
   - Current: Sequential test execution
   - Future: Concurrent tests with configurable parallelism
   - Consideration: Reachability Analyzer rate limits

2. **Cross-Region Testing**
   - Current: Single-region per account
   - Future: Multi-region golden path, inter-region peering tests
   - Consideration: Reachability Analyzer is region-scoped

3. **Incremental Discovery**
   - Current: Full discovery each run
   - Future: Delta discovery, only scan changed accounts
   - Consideration: Detect infra drift vs. intentional changes

4. **Custom Test Assertions**
   - Current: Binary reachability (pass/fail)
   - Future: Custom assertions (latency thresholds, specific hop paths)
   - Consideration: Requires actual traffic testing, not static analysis

5. **Alerting Integration**
   - Current: CloudWatch metrics only
   - Future: SNS notifications, PagerDuty, Slack integration
   - Consideration: Alert fatigue, actionable alerts only

### Design Decisions

#### Hub-and-Spoke TGW Testing Strategy

In a hub-and-spoke Transit Gateway architecture:

```
Spoke A ←→ TGW ←→ Hub VPC ←→ TGW ←→ Spoke B
```

**Question:** Should we test direct spoke-to-spoke paths (A→B, B→A) in addition to spoke-hub paths?

**Decision:** No. Testing spoke↔hub links is sufficient for regression detection.

**Rationale:**

The current implementation discovers and tests connectivity based on TGW route tables:
- Spoke A → Hub ✓
- Hub → Spoke A ✓
- Spoke B → Hub ✓
- Hub → Spoke B ✓

This is sufficient for regression testing because:

1. **Route breakage**: If Spoke A → Spoke B breaks, it's because either:
   - Spoke A → Hub broke (caught by existing test)
   - Hub → Spoke B broke (caught by existing test)

2. **Security group changes**: Any SG change blocking spoke-to-spoke would also affect:
   - Spoke's egress to Hub, OR
   - Hub's ingress/egress, OR
   - Spoke's ingress from Hub

3. **TGW attachment issues**: Would manifest in spoke ↔ hub tests

4. **NACL changes**: Would affect the individual spoke↔hub links

5. **Transitive property**: If A→Hub AND Hub→B both pass, then A→B via Hub will work

**When spoke-to-spoke tests would be redundant:**
- They would only fail if one of the underlying spoke↔hub tests also fails
- Adding them increases test count without improving regression detection

**Edge cases covered:**
- Hub route table misconfiguration (not forwarding between spokes) → shows up as Hub→Spoke failures
- Asymmetric routing issues → caught by testing both directions of each link

### Known Limitations

| Limitation | Workaround |
|------------|------------|
| No DNS resolution testing | Use Route 53 Resolver query logs |
| No actual throughput testing | Use VPC Flow Logs, CloudWatch metrics |
| VPN tests are status-only | Supplement with on-prem monitoring |
| Cross-region not supported | Run separate discovery per region |

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=term-missing

# Run specific module tests
pytest tests/test_models.py
pytest tests/test_cli.py
pytest tests/test_auth.py
pytest tests/test_orchestrator.py
pytest tests/test_reachability.py
pytest tests/test_reporting.py
```

**Test coverage:**
- `test_models.py` - Data models and enums
- `test_auth.py` - Authentication and session management
- `test_cli.py` - CLI argument parsing
- `test_orchestrator.py` - Test coordination logic
- `test_reachability.py` - AWS Reachability Analyzer tests
- `test_reporting.py` - CloudWatch/S3 publishing

## File Structure

```
aft-network-testing/
├── tests/
│   ├── conftest.py      # Shared fixtures
│   ├── test_models.py
│   ├── test_auth.py
│   ├── test_cli.py
│   ├── test_orchestrator.py
│   ├── test_reachability.py
│   └── test_reporting.py
├── src/
│   ├── cli.py           # Entry point
│   ├── orchestrator.py  # Test coordination
│   ├── auth.py          # Authentication
│   ├── models.py        # Data models
│   ├── baseline.py      # VPC discovery
│   ├── connectivity.py  # Connection discovery
│   ├── reachability.py  # Test execution
│   └── reporting.py     # Results publishing
├── config/
│   ├── accounts.yaml    # Account definitions
│   └── golden_path.yaml # Generated baseline
├── deployment/
│   ├── terraform/       # Lambda, S3, IAM
│   └── ci-cd/           # CodeBuild pipeline
└── docs/
    ├── DESIGN.md        # This document
    ├── QUICKSTART.md    # Getting started
    └── solution.md      # Use case examples
```

## API Reference

### AuthConfig

```python
auth = AuthConfig(
    mode=ExecutionMode.LOCAL,
    profile_name="aft-admin",      # OR
    profile_pattern="{account_id}",
    role_name="AWSAFTExecution",
    region="us-east-1"
)

session = auth.get_hub_session()
session = auth.get_account_session(account_id)
```

### AFTTestOrchestrator

```python
orchestrator = AFTTestOrchestrator(
    auth_config=auth,
    golden_path_file="./golden_path.yaml",
    s3_bucket="my-results-bucket"  # Optional
)

# Discovery
golden_path = orchestrator.discover_baseline(
    accounts=[AccountConfig(...)],
    tgw_id="tgw-xxx",           # Optional, auto-discovered
    connection_types=["tgw", "peering"]  # Optional, defaults to all
)

# Testing
summary = orchestrator.run_tests(
    accounts=[AccountConfig(...)],
    phase=TestPhase.POST_RELEASE,
    parallel=True,
    publish=False  # Set True to publish to CloudWatch/S3
)
```

### ReachabilityTester

```python
tester = ReachabilityTester(auth_config=auth)

result = tester.test_connectivity(
    connection_type=ConnectionType.TRANSIT_GATEWAY,
    source_vpc="vpc-abc",
    dest_vpc="vpc-def",
    connection_id="tgw-attach-xxx",
    protocol="tcp",
    port=443
)
# Returns: TestCase(name, result, message, duration_ms, metadata)
```
