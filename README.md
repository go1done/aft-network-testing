# AFT Network Testing Framework

Comprehensive network testing for AWS Control Tower AFT environments.

## Features

- ✅ Multi-connection type discovery (TGW, Peering, VPN, PrivateLink)
- ✅ Automatic baseline generation
- ✅ Golden path compliance validation
- ✅ AWS native testing (Reachability Analyzer)
- ✅ CloudWatch metrics and S3 results
- ✅ Local and AWS execution modes

## Why Baseline Testing Matters

Cloud infrastructure teams need baseline testing for effective QA. Here's why:

### Drift Detection

Cloud environments change constantly—manual fixes, Terraform applies, auto-scaling, security patches. A baseline captures "known good" state so you can detect when reality diverges from intent.

### Regression Prevention

Infrastructure changes (new VPCs, route table updates, security group modifications) can silently break connectivity. Without a baseline, you don't know what *should* work until something fails in production.

### Blast Radius Assessment

When making changes, you can run pre-flight tests against the baseline to predict what will break *before* applying changes.

### Compliance & Audit

Baselines provide evidence that connectivity meets requirements at a point in time. Useful for SOC2, PCI, and HIPAA audits.

### QA Without vs. With Baselines

| Without Baseline | With Baseline |
|------------------|---------------|
| "It works on my account" | Reproducible expected state |
| Manual verification | Automated regression tests |
| Reactive troubleshooting | Proactive drift alerts |
| Unknown unknowns | Explicit connectivity contracts |

**Bottom line**: Baselines shift QA from "did it deploy?" to "does it still work correctly?"—which is where the real production issues hide.

## Which Accounts to Discover

In an AFT environment, you need to include **all accounts that participate in network connectivity**:

### Minimum Required Accounts

| Account Type | Why Needed | Example |
|--------------|------------|---------|
| **Network/Shared Services** | Owns Transit Gateway, central routing | `network-hub` |
| **Workload Accounts** | VPCs attached to TGW or peered | `prod-app1`, `dev-app1` |

### Account Selection by Connection Type

| Connection Type | Accounts to Include |
|-----------------|---------------------|
| **Transit Gateway** | All accounts with VPCs attached to TGW |
| **VPC Peering** | Both requester and accepter accounts |
| **VPN** | Accounts with VPN connections |
| **PrivateLink** | Both provider and consumer accounts |

### Practical Example

For a typical AFT landing zone:

```yaml
# config/accounts.yaml
accounts:
  # Network Hub (owns TGW)
  - account_id: "111111111111"
    account_name: "network-hub"
    region: "us-east-1"

  # Production workloads
  - account_id: "222222222222"
    account_name: "prod-app1"
    region: "us-east-1"

  - account_id: "333333333333"
    account_name: "prod-app2"
    region: "us-east-1"

  # Non-prod workloads
  - account_id: "444444444444"
    account_name: "dev-app1"
    region: "us-east-1"
```

### Discovery Behavior

The framework auto-discovers:
- **TGW IDs** from VPC attachments (no need to specify `--tgw-id` unless you want a specific one)
- **VPC IDs** if not provided in config
- **All connection types** unless filtered with `--connection-types`

### What Gets Discovered Per Account

For each account in your config, the framework queries:
1. VPC configuration (CIDR, subnets, route tables)
2. TGW attachments
3. VPC peering connections
4. VPN connections
5. PrivateLink endpoints

**Tip**: Start with accounts you know have connectivity, run discover, then check if any discovered connections reference accounts not in your config—add those accounts and re-run.

## Reachability Analyzer Coverage

AWS Reachability Analyzer is powerful but **does not cover all baseline scenarios**. The framework uses different testing methods per connection type:

### What Reachability Analyzer Tests

| Connection Type | Test Method | What It Validates |
|-----------------|-------------|-------------------|
| **Transit Gateway** | Reachability Analyzer | Full path analysis (route tables, SGs, NACLs) |
| **VPC Peering** | Reachability Analyzer | Full path analysis between ENIs |
| **VPN** | API status check | Tunnel UP/DOWN status only |
| **PrivateLink** | API status check | Endpoint state and ENI availability |

### Reachability Analyzer Limitations

| What It Does | What It Doesn't Do |
|--------------|-------------------|
| Static path analysis | Actual traffic testing |
| Route table validation | DNS resolution |
| Security group rules | Application-layer connectivity |
| NACL rules | TLS/certificate validation |
| Hop-by-hop analysis | Latency/performance |
| Cross-VPC paths | Cross-region paths |

### Gaps This Framework Fills

The framework supplements Reachability Analyzer with:

1. **VPN Tunnel Status** - Checks `VgwTelemetry` for tunnel health (Reachability Analyzer can't test VPN tunnels to on-prem)

2. **PrivateLink State** - Validates endpoint availability and ENI health (Reachability Analyzer needs specific source/dest, not service endpoints)

3. **Baseline Drift Detection** - Compares current state against golden path (Reachability Analyzer only shows current reachability)

4. **Multi-Connection Correlation** - Tests all connection types in one run with unified reporting

### What's Still Not Covered

Even with this framework, you may need additional testing for:

| Gap | Why | Supplement With |
|-----|-----|-----------------|
| **DNS resolution** | Reachability Analyzer is IP-based | Route 53 Resolver query logs |
| **Application health** | Network != application working | Health checks, synthetic monitoring |
| **Actual throughput** | Static analysis only | VPC Flow Logs, CloudWatch metrics |
| **Cross-region** | Reachability Analyzer is single-region | Inter-region peering tests separately |
| **On-prem reachability** | Can't reach beyond AWS | Ping/traceroute from EC2, VPN monitoring |

### Recommended Testing Strategy

```
┌─────────────────────────────────────────────────────────────┐
│                    BASELINE TESTING LAYERS                  │
├─────────────────────────────────────────────────────────────┤
│  Layer 1: Network Path (This Framework)                     │
│  ├── Reachability Analyzer (TGW, Peering)                   │
│  ├── VPN tunnel status                                      │
│  └── PrivateLink endpoint state                             │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: Traffic Validation                                │
│  ├── VPC Flow Logs analysis                                 │
│  └── CloudWatch network metrics                             │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: Application Health                                │
│  ├── Synthetic canaries (CloudWatch Synthetics)             │
│  ├── Load balancer health checks                            │
│  └── Application-level ping/health endpoints                │
└─────────────────────────────────────────────────────────────┘
```

**Bottom line**: Use Reachability Analyzer for "can packets flow?" and supplement with other tools for "is the application actually working?"

## Quick Start

\`\`\`bash
# Install
pip install -r requirements.txt
pip install -e .

# Configure
cp config/accounts.yaml.example config/accounts.yaml
# Edit accounts.yaml

# Discover
aft-test --phase discover --accounts-file config/accounts.yaml --tgw-id tgw-xxxxx

# Test
aft-test --phase post-release --accounts-file config/accounts.yaml
\`\`\`

## Documentation

See `docs/` folder for detailed guides.