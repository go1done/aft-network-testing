# AFT Network Connectivity Testing Guide

**For AWS Cloud Infrastructure Team**

---

## Table of Contents

1. [Overview](#overview)
2. [Key Features](#key-features)
3. [Architecture](#architecture)
4. [Prerequisites](#prerequisites)
5. [Getting Started](#getting-started)
6. [Configuration](#configuration)
7. [Test Phases](#test-phases)
8. [Connection Types](#connection-types)
9. [Authentication](#authentication)
10. [CLI Reference](#cli-reference)
11. [Workflows](#workflows)
12. [Reporting & Monitoring](#reporting--monitoring)
13. [Troubleshooting](#troubleshooting)
14. [Best Practices](#best-practices)
15. [FAQ](#faq)

---

## Overview

The AFT Network Testing Framework is an automated network connectivity testing solution designed for AWS Control Tower AFT (Account Factory for Terraform) environments. It validates multi-account, multi-region VPC connectivity using AWS Reachability Analyzer and native AWS APIs.

### What It Does

| Capability | Description |
|------------|-------------|
| **Auto-Discovery** | Automatically discovers VPCs, Transit Gateways, peering connections, VPNs, and PrivateLink endpoints |
| **Baseline Generation** | Creates a "golden path" baseline of expected connectivity patterns |
| **Connectivity Testing** | Validates actual connectivity against expected baseline using AWS Reachability Analyzer |
| **Drift Detection** | Identifies configuration drift before and after infrastructure changes |
| **Multi-Account Support** | Tests connectivity across multiple AWS accounts in your organization |

### When to Use

- **Before Terraform Apply**: Run pre-release tests to validate current state
- **After Terraform Apply**: Run post-release tests to verify changes didn't break connectivity
- **Periodic Audits**: Schedule regular connectivity audits via Lambda/EventBridge
- **Incident Investigation**: Diagnose connectivity issues between accounts

---

## Key Features

### Multi-Connection Type Support

The framework discovers and tests all major AWS network connection types:

- **Transit Gateway (TGW)** - Hub-and-spoke connectivity
- **VPC Peering** - Direct VPC-to-VPC connections
- **VPN** - Site-to-site VPN tunnels
- **PrivateLink** - VPC endpoints for AWS services and custom services

### Automatic Discovery

- **VPC IDs**: Auto-discovered from each account if not specified
- **Transit Gateway**: Auto-discovered from VPC attachments across all accounts
- **Connection Types**: All connection types discovered without manual configuration
- **Traffic Patterns**: Discovered from VPC Flow Logs (when enabled)

### Smart Testing

- Uses appropriate testing method for each connection type
- Supports protocol-level and port-specific tests
- Reuses existing Network Insights Paths (idempotent)
- Bi-directional testing for peering connections

---

## Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │           CLI (aft-test)            │
                                    │  • Argument parsing                 │
                                    │  • Phase dispatch                   │
                                    │  • Account loading                  │
                                    └──────────────┬──────────────────────┘
                                                   │
                                    ┌──────────────▼──────────────────────┐
                                    │          Orchestrator               │
                                    │  • Coordinates test phases          │
                                    │  • Manages golden path              │
                                    │  • Generates test matrix            │
                                    └──────────────┬──────────────────────┘
                                                   │
                    ┌──────────────────────────────┼──────────────────────────────┐
                    │                              │                              │
         ┌──────────▼──────────┐       ┌──────────▼──────────┐       ┌──────────▼──────────┐
         │      Discovery      │       │       Testing       │       │      Reporting      │
         │  • VPC baselines    │       │  • Reachability     │       │  • CloudWatch       │
         │  • TGW topology     │       │    Analyzer         │       │  • S3 results       │
         │  • Peering/VPN/VPCE │       │  • VPN tunnel       │       │  • Console output   │
         │  • Flow logs        │       │    status           │       │                     │
         └─────────────────────┘       └─────────────────────┘       └─────────────────────┘
                    │                              │
                    │         ┌────────────────────┘
                    │         │
         ┌──────────▼─────────▼──────────┐
         │     Authentication (Auth)     │
         │  • SAML/SSO profiles          │
         │  • STS role assumption        │
         │  • Session caching            │
         └───────────────────────────────┘
```

### Data Flow

```
accounts.yaml → Discovery → golden_path.yaml → Testing → Results (Console/CloudWatch/S3)
```

---

## Prerequisites

### Required Permissions

The execution role needs the following permissions in all target accounts:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeRouteTables",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeNetworkAcls",
        "ec2:DescribeTransitGatewayAttachments",
        "ec2:DescribeTransitGatewayRouteTables",
        "ec2:DescribeVpcPeeringConnections",
        "ec2:DescribeVpnConnections",
        "ec2:DescribeVpcEndpoints",
        "ec2:DescribeNetworkInterfaces",
        "ec2:CreateNetworkInsightsPath",
        "ec2:DeleteNetworkInsightsPath",
        "ec2:StartNetworkInsightsAnalysis",
        "ec2:DescribeNetworkInsightsAnalyses",
        "ec2:DescribeNetworkInsightsPaths",
        "logs:FilterLogEvents",
        "logs:DescribeLogGroups"
      ],
      "Resource": "*"
    }
  ]
}
```

### Software Requirements

- Python 3.9+
- AWS CLI v2 configured with appropriate profiles
- Network connectivity to AWS APIs

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd aft-network-testing

# Install dependencies
pip install -r requirements.txt
pip install -e .

# Verify installation
aft-test --help
```

---

## Getting Started

### Quick Start (5 Minutes)

**Step 1: Create accounts configuration**

```bash
mkdir -p config
cat > config/accounts.yaml << 'EOF'
accounts:
  - account_id: "111111111111"
    account_name: "network-hub"
    region: "us-east-1"

  - account_id: "222222222222"
    account_name: "prod-workload"
    region: "us-east-1"

  - account_id: "333333333333"
    account_name: "dev-workload"
    region: "us-east-1"
EOF
```

**Step 2: Run discovery to generate golden path**

```bash
aft-test --mode local --profile your-sso-profile \
  --phase discover \
  --accounts-file config/accounts.yaml
```

**Step 3: Review the generated golden path**

```bash
cat golden_path.yaml
```

**Step 4: Run connectivity tests**

```bash
aft-test --mode local --profile your-sso-profile \
  --phase post-release \
  --accounts-file config/accounts.yaml \
  --golden-path golden_path.yaml
```

---

## Configuration

### accounts.yaml

The accounts file defines which AWS accounts to include in testing.

```yaml
accounts:
  # Minimal configuration (VPC auto-discovered)
  - account_id: "111111111111"
    account_name: "network-hub"
    region: "us-east-1"

  # Full configuration
  - account_id: "222222222222"
    account_name: "prod-workload"
    region: "us-east-1"
    vpc_id: "vpc-abc123"              # Optional: specify exact VPC
    tgw_id: "tgw-xyz789"              # Optional: specify TGW
    expected_routes: []               # Optional: expected route destinations
    test_ports: [443, 22, 3306]       # Optional: specific ports to test
```

| Field | Required | Description |
|-------|----------|-------------|
| `account_id` | Yes | 12-digit AWS account ID |
| `account_name` | Yes | Human-readable account name |
| `region` | No | AWS region (default: us-east-1) |
| `vpc_id` | No | Specific VPC ID (auto-discovered if omitted) |
| `tgw_id` | No | Transit Gateway ID (auto-discovered if omitted) |
| `expected_routes` | No | List of expected route destinations |
| `test_ports` | No | Specific ports to test |

### golden_path.yaml

The golden path file is **auto-generated** during the discovery phase. It captures:

- Expected VPC configurations (DNS, subnets, AZs)
- Transit Gateway settings
- All connectivity patterns discovered
- Traffic patterns from Flow Logs

**Example structure:**

```yaml
version: "1.0"
generated_at: "2024-01-15T10:30:00Z"
based_on_accounts: 3

expected_configuration:
  vpc:
    dns_support: true
    dns_hostnames: true
    min_subnets: 2
    min_availability_zones: 2

  transit_gateway:
    required: true
    expected_state: "available"

connectivity:
  tgw_id: "tgw-xyz789"
  total_paths: 10
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
      dest_account_name: "prod-workload"
      connection_type: "tgw"
      expected_reachable: true
```

---

## Test Phases

### Phase Overview

| Phase | Purpose | When to Use |
|-------|---------|-------------|
| `discover` | Generate golden path baseline | Initial setup, after major infrastructure changes |
| `pre-release` | Validate current state before changes | Before Terraform apply |
| `post-release` | Verify connectivity after changes | After Terraform apply |

### discover

Scans all accounts and generates a comprehensive connectivity baseline.

```bash
aft-test --mode local --profile your-profile \
  --phase discover \
  --accounts-file config/accounts.yaml
```

**What it does:**
1. Scans VPC configurations in each account
2. Discovers Transit Gateway attachments and routes
3. Finds VPC peering connections
4. Identifies VPN connections and checks tunnel status
5. Lists VPC endpoints (PrivateLink)
6. Analyzes VPC Flow Logs for traffic patterns (if enabled)
7. Generates `golden_path.yaml`

### pre-release

Validates current connectivity matches the golden path before making changes.

```bash
aft-test --mode local --profile your-profile \
  --phase pre-release \
  --accounts-file config/accounts.yaml \
  --golden-path config/golden_path.yaml
```

**Use case:** Run this before `terraform apply` to ensure the baseline is stable.

### post-release

Verifies connectivity after infrastructure changes.

```bash
aft-test --mode local --profile your-profile \
  --phase post-release \
  --accounts-file config/accounts.yaml \
  --golden-path config/golden_path.yaml \
  --publish-results \
  --s3-bucket my-results-bucket
```

**Use case:** Run this after `terraform apply` to verify changes didn't break connectivity.

---

## Connection Types

### Transit Gateway (TGW)

Hub-and-spoke architecture for connecting multiple VPCs.

**Discovery:** Finds TGW attachments from all accounts
**Testing:** Uses AWS Reachability Analyzer for full path analysis

```bash
# Auto-discover TGW from VPC attachments
aft-test --mode local --profile your-profile \
  --phase discover \
  --accounts-file config/accounts.yaml

# Or specify a specific TGW
aft-test --mode local --profile your-profile \
  --phase discover \
  --accounts-file config/accounts.yaml \
  --tgw-id tgw-xyz789
```

### VPC Peering

Direct connections between two VPCs.

**Discovery:** Queries VPC peering connections API
**Testing:** Uses Reachability Analyzer with ENIs for bi-directional testing

### VPN

Site-to-site VPN connections.

**Discovery:** Queries VPN connections API
**Testing:** Checks tunnel status via VgwTelemetry (UP/DOWN)

> **Note:** Reachability Analyzer cannot test VPN tunnels directly; the framework uses tunnel status checks instead.

### PrivateLink (VPC Endpoints)

Private connectivity to AWS services and custom endpoints.

**Discovery:** Lists VPC endpoints in each account
**Testing:** Verifies endpoint state and ENI health

### Filtering Connection Types

Test only specific connection types:

```bash
# Only TGW and peering
aft-test --mode local --profile your-profile \
  --phase discover \
  --accounts-file config/accounts.yaml \
  --connection-types tgw,peering

# All types (default)
aft-test --mode local --profile your-profile \
  --phase discover \
  --accounts-file config/accounts.yaml \
  --connection-types all
```

---

## Authentication

### Option 1: Single SSO Profile (Recommended for AFT)

Use a single SSO profile with cross-account access permissions.

```bash
# AWS SSO login
aws sso login --profile aft-admin

# Run tests
aft-test --mode local --profile aft-admin \
  --phase discover \
  --accounts-file config/accounts.yaml
```

**Requirements:**
- SSO profile configured in `~/.aws/config`
- Profile has permissions in all target accounts (via IAM Identity Center)

### Option 2: Per-Account Profiles

Use separate profiles for each account.

```bash
# In ~/.aws/credentials
[111111111111]
aws_access_key_id = ...
aws_secret_access_key = ...

[222222222222]
aws_access_key_id = ...
aws_secret_access_key = ...
```

```bash
# Run with profile pattern
aft-test --mode local --profile-pattern "{account_id}" \
  --phase discover \
  --accounts-file config/accounts.yaml
```

**Supported patterns:**
- `{account_id}` - Uses account ID as profile name
- `sso-{account_id}` - Prefix + account ID
- `{account_name}` - Uses account name as profile name

### Option 3: AWS Mode (Lambda/CodeBuild)

For automated execution in AWS environments.

```bash
aft-test --mode aws \
  --phase discover \
  --accounts-file config/accounts.yaml \
  --role AWSAFTExecution
```

**How it works:**
1. Uses execution environment credentials (Lambda role, CodeBuild role)
2. Assumes specified role in each target account
3. Sessions cached for 50 minutes (before 1-hour expiry)

---

## CLI Reference

### Full Command Syntax

```bash
aft-test [OPTIONS]
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--mode` | Execution mode: `local`, `aws`, `codebuild` | `local` |
| `--phase` | Test phase: `discover`, `pre-release`, `post-release` | Required |
| `--profile` | AWS CLI profile name (local mode) | - |
| `--profile-pattern` | Profile pattern for per-account auth | - |
| `--role` | IAM role to assume (aws mode) | `AWSAFTExecution` |
| `--region` | AWS region | `us-west-2` |
| `--accounts-file` | Path to accounts YAML | `config/accounts.yaml` |
| `--golden-path` | Path to golden path YAML | `./golden_path.yaml` |
| `--tgw-id` | Specific Transit Gateway ID | Auto-discovered |
| `--connection-types` | Types to test: `all`, or comma-separated | `all` |
| `--publish-results` | Publish to CloudWatch/S3 | `false` |
| `--s3-bucket` | S3 bucket for results | - |
| `--parallel` | Run tests in parallel | `true` |
| `--dry-run` | Validate config without executing | `false` |
| `--verbose`, `-v` | Enable verbose output | `false` |

### Examples

```bash
# Validate configuration only
aft-test --dry-run --phase discover --accounts-file config/accounts.yaml

# Discovery with verbose output
aft-test --mode local --profile aft-admin \
  --phase discover \
  --accounts-file config/accounts.yaml \
  --verbose

# Full post-release test with reporting
aft-test --mode local --profile aft-admin \
  --phase post-release \
  --accounts-file config/accounts.yaml \
  --golden-path config/golden_path.yaml \
  --publish-results \
  --s3-bucket my-test-results
```

---

## Workflows

### Workflow 1: Initial Setup

Use when setting up connectivity testing for the first time.

```bash
# 1. Create accounts configuration
vim config/accounts.yaml

# 2. Validate configuration
aft-test --dry-run --phase discover --accounts-file config/accounts.yaml

# 3. Run discovery
aft-test --mode local --profile aft-admin \
  --phase discover \
  --accounts-file config/accounts.yaml

# 4. Review and commit golden path
cat golden_path.yaml
cp golden_path.yaml config/golden_path.yaml
git add config/golden_path.yaml
git commit -m "Add network connectivity baseline"
```

### Workflow 2: Pre/Post Release Testing

Use as part of your infrastructure change process.

```bash
# BEFORE terraform apply
aft-test --mode local --profile aft-admin \
  --phase pre-release \
  --accounts-file config/accounts.yaml \
  --golden-path config/golden_path.yaml

# Check results - ensure all tests pass

# RUN infrastructure changes
terraform apply

# AFTER terraform apply
aft-test --mode local --profile aft-admin \
  --phase post-release \
  --accounts-file config/accounts.yaml \
  --golden-path config/golden_path.yaml \
  --publish-results
```

### Workflow 3: Updating Golden Path After Intentional Changes

When you intentionally change the network topology:

```bash
# 1. Make infrastructure changes
terraform apply

# 2. Re-run discovery to update golden path
aft-test --mode local --profile aft-admin \
  --phase discover \
  --accounts-file config/accounts.yaml

# 3. Review changes
diff config/golden_path.yaml golden_path.yaml

# 4. Accept new baseline
cp golden_path.yaml config/golden_path.yaml
git add config/golden_path.yaml
git commit -m "Update network baseline after adding new VPC"
```

### Workflow 4: CI/CD Integration (CodeBuild)

```yaml
# buildspec.yml
version: 0.2

phases:
  install:
    commands:
      - pip install -r requirements.txt
      - pip install -e .

  pre_build:
    commands:
      - echo "Running pre-release connectivity tests..."
      - aft-test --mode codebuild --phase pre-release \
          --accounts-file config/accounts.yaml \
          --golden-path config/golden_path.yaml

  build:
    commands:
      - terraform apply -auto-approve

  post_build:
    commands:
      - echo "Running post-release connectivity tests..."
      - aft-test --mode codebuild --phase post-release \
          --accounts-file config/accounts.yaml \
          --golden-path config/golden_path.yaml \
          --publish-results \
          --s3-bucket $RESULTS_BUCKET
```

### Workflow 5: Scheduled Testing (Lambda)

Deploy the Lambda function and schedule via EventBridge:

```bash
# Deploy infrastructure
cd deployment/terraform
terraform init
terraform apply

# Lambda runs on schedule and publishes results to CloudWatch/S3
```

---

## Reporting & Monitoring

### Console Output

Default output shows test progress and results:

```
===============================================================
PHASE: POST-RELEASE
===============================================================
[1/4] Transit Gateway Connectivity
  ✓ Testing 5 TGW paths
  ✓ 5/5 paths reachable

[2/4] VPC Peering Connectivity
  ✓ Testing 2 peering paths
  ✓ 2/2 paths reachable

[3/4] VPN Connectivity
  ✓ Testing 1 VPN connection
  ✓ 1/1 tunnels UP

[4/4] PrivateLink Connectivity
  ✓ Testing 2 endpoints
  ✓ 2/2 endpoints available

===============================================================
SUMMARY
===============================================================
Total Tests: 10
Passed:      10
Failed:      0
Warnings:    0

Result: ALL TESTS PASSED
===============================================================
```

### CloudWatch Metrics

Enable with `--publish-results`:

**Namespace:** `AFT/VPCTests`

| Metric | Description | Dimensions |
|--------|-------------|------------|
| `TestsPassed` | Count of passing tests | Phase |
| `TestsFailed` | Count of failing tests | Phase |
| `TestsWarnings` | Count of warnings | Phase |
| `TestsSkipped` | Count of skipped tests | Phase |
| `TotalTests` | Total test count | Phase |
| `TestDuration` | Execution time (seconds) | Phase |

**Create CloudWatch Alarms:**

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "NetworkConnectivity-TestsFailed" \
  --metric-name TestsFailed \
  --namespace AFT/VPCTests \
  --statistic Sum \
  --period 300 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:us-east-1:123456789012:alerts
```

### S3 Results

Enable with `--publish-results --s3-bucket <bucket>`:

**Path:** `s3://{bucket}/vpc-tests/{phase}/{timestamp}.json`

**Example JSON:**

```json
{
  "phase": "post-release",
  "start_time": "2024-01-15T10:00:00Z",
  "end_time": "2024-01-15T10:05:00Z",
  "duration_seconds": 300.0,
  "total_tests": 10,
  "passed": 10,
  "failed": 0,
  "warnings": 0,
  "skipped": 0,
  "results": [
    {
      "name": "TGW: network-hub -> prod-workload (tcp:443)",
      "result": "PASS",
      "message": "Path reachable",
      "duration_ms": 1500,
      "metadata": {
        "analysis_id": "nia-abc123",
        "source_vpc": "vpc-111",
        "dest_vpc": "vpc-222",
        "connection_type": "tgw"
      }
    }
  ]
}
```

---

## Troubleshooting

### Common Issues

#### "No VPCs found in account"

**Cause:** No non-default VPCs exist, or permissions issue.

**Solution:**
1. Verify VPCs exist in the account
2. Check IAM permissions include `ec2:DescribeVpcs`
3. Explicitly specify `vpc_id` in accounts.yaml

#### "Transit Gateway not found"

**Cause:** No TGW attachments found in any account.

**Solution:**
1. Verify TGW attachments exist
2. Use `--tgw-id` to specify the TGW explicitly
3. Check IAM permissions include `ec2:DescribeTransitGatewayAttachments`

#### "Authentication failed"

**Cause:** Invalid or expired credentials.

**Solution:**
1. Run `aws sso login --profile <profile>` for SSO profiles
2. Verify profile name matches `--profile` argument
3. For AWS mode, verify the execution role can assume target roles

#### "Reachability Analyzer shows UNREACHABLE"

**Cause:** Actual network configuration blocks traffic.

**Investigation:**
1. Check the analysis details in AWS Console (Network Insights)
2. Review security groups, NACLs, route tables
3. Verify TGW route tables have correct routes
4. Check VPC peering route propagation

#### "VPN tunnel shows DOWN"

**Cause:** VPN tunnel is not established.

**Solution:**
1. Check VPN connection status in AWS Console
2. Verify customer gateway configuration
3. Review VPN logs for connection errors

### Debug Mode

Enable verbose output for detailed logging:

```bash
aft-test --mode local --profile your-profile \
  --phase discover \
  --accounts-file config/accounts.yaml \
  --verbose
```

### Dry Run Validation

Validate configuration without executing:

```bash
aft-test --dry-run \
  --phase discover \
  --accounts-file config/accounts.yaml
```

---

## Best Practices

### Configuration Management

1. **Version control your configurations**
   - Commit `accounts.yaml` and `golden_path.yaml` to git
   - Review golden path changes in pull requests

2. **Keep accounts.yaml minimal**
   - Let the framework auto-discover VPCs and TGWs
   - Only specify IDs when you need to test specific resources

3. **Update golden path after intentional changes**
   - Re-run discovery when you add/remove accounts or connections
   - Review diff before accepting new baseline

### Testing Strategy

1. **Run pre-release tests before all infrastructure changes**
   - Catches drift before it compounds with new changes
   - Establishes known-good baseline

2. **Run post-release tests after all infrastructure changes**
   - Validates changes didn't break existing connectivity
   - Updates monitoring dashboards

3. **Schedule regular connectivity audits**
   - Use Lambda + EventBridge for daily/weekly tests
   - Alert on failures via CloudWatch alarms

### Security

1. **Use least-privilege IAM roles**
   - Only grant required permissions (see Prerequisites)
   - Use separate roles for read-only vs. analysis operations

2. **Secure results storage**
   - Enable S3 bucket encryption
   - Restrict bucket access to authorized principals
   - Enable S3 access logging

3. **Protect golden path files**
   - Store in private repository
   - Review changes before merging

### Performance

1. **Use connection type filtering for focused tests**
   - Filter to specific types when debugging issues
   - Run full tests for comprehensive validation

2. **Leverage parallel execution**
   - Default `--parallel` mode tests multiple paths concurrently
   - Reduces overall test duration

---

## FAQ

**Q: How long do tests take to run?**

A: Depends on the number of accounts and connections. Typical runs:
- 5 accounts, 10 paths: 2-3 minutes
- 20 accounts, 50 paths: 8-10 minutes
- Reachability Analyzer analyses take ~30-60 seconds each

**Q: Does this create any resources in my accounts?**

A: Yes, temporarily:
- Network Insights Paths (for Reachability Analyzer)
- These are cleaned up after analysis completes
- No persistent resources remain

**Q: What's the cost of running these tests?**

A: Minimal:
- Reachability Analyzer: No direct cost (included in EC2)
- CloudWatch metrics: Standard pricing (~$0.30/metric/month)
- S3 storage: Standard pricing for results files

**Q: Can I test connectivity to on-premises?**

A: Partially:
- VPN tunnel status can be checked
- Reachability Analyzer cannot test beyond AWS network boundary
- On-prem connectivity requires additional validation methods

**Q: How do I add a new account to testing?**

A:
1. Add account to `accounts.yaml`
2. Re-run discovery phase
3. Review and commit updated golden path

**Q: Can I exclude certain paths from testing?**

A: Currently, filtering is done at the connection type level. For path-level exclusions, edit the golden path YAML to remove specific patterns.

---

## Support

For issues or questions:
- Review this documentation
- Check the [Troubleshooting](#troubleshooting) section
- Contact the Cloud Infrastructure team

---

*Document Version: 1.0*
*Last Updated: 2024*
