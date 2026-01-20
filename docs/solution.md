# Multi-Connection Type AFT Testing

## 🎯 Problem Solved

**Your Question:** *"There are also some cases using peering, like backup database from one VPC to another. Is it worthwhile to consider different connection/attachment types?"*

**Answer:** **YES! Absolutely worthwhile.** Here's the complete solution.

---

## 🔌 Connection Types Supported

| Connection Type | Use Case | Discovery Method | Test Method |
|----------------|----------|------------------|-------------|
| **Transit Gateway** | General inter-VPC, hub-spoke | TGW route tables | Reachability Analyzer |
| **VPC Peering** | Database backups, low-latency direct | Peering API | Reachability Analyzer |
| **VPN** | On-premises hybrid | VPN API | Tunnel status |
| **PrivateLink** | Service endpoints | VPC Endpoints API | Endpoint status |

---

## 📊 Example Discovered Topology

### Your Infrastructure
```
Production Account:
  vpc-prod-app
    ├─ via TGW ──────→ vpc-shared-services (general traffic)
    ├─ via TGW ──────→ vpc-monitoring (metrics)
    └─ via PEERING ──→ vpc-prod-backup (database backup - 10 Gbps direct)

QA Account:
  vpc-qa
    ├─ via TGW ──────→ vpc-shared-services
    └─ via VPN ──────→ On-Premises (testing from office)

Shared Services:
  vpc-shared-services
    └─ via PrivateLink → S3, DynamoDB endpoints
```

### Discovered Golden Path
```yaml
connectivity:
  total_paths: 8
  by_connection_type:
    tgw: 5        # Most traffic
    peering: 1    # Database backup
    vpn: 1        # Hybrid
    privatelink: 1 # AWS services

  patterns:
    # TGW connections
    - source_vpc: vpc-prod-app
      dest_vpc: vpc-shared-services
      connection_type: tgw
      connection_id: tgw-xyz789
      use_case: general
      traffic_observed: true
      ports_observed: [443, 22, 9200]
    
    # VPC Peering (database backup)
    - source_vpc: vpc-prod-app
      dest_vpc: vpc-prod-backup
      connection_type: pcx
      connection_id: pcx-abc123
      use_case: database-backup
      traffic_observed: true
      ports_observed: [3306, 5432]  # MySQL, PostgreSQL
      packet_count: 1500000  # Heavy traffic!
    
    # VPN (on-premises)
    - source_vpc: vpc-qa
      dest_vpc: on-premises
      connection_type: vpn
      connection_id: vpn-def456
      use_case: hybrid-connectivity
      traffic_observed: true
    
    # PrivateLink
    - source_vpc: vpc-shared-services
      dest_vpc: privatelink-service
      connection_type: vpce
      connection_id: vpce-s3
      use_case: service-access
```

---

## 🚀 Discovery Workflow

### Step 1: Auto-Discover All Connection Types

```bash
python aft_test_orchestrator.py \
  --phase discover \
  --accounts-file accounts.yaml \
  --tgw-id tgw-xyz789  # Optional, discovers peering/VPN regardless
```

**What Gets Discovered:**

```
[1/4] Transit Gateway Connectivity
  ✓ Found 3 VPC attachments
  ✓ Found 2 route tables
  ✓ Discovered 5 TGW connectivity paths

[2/4] VPC Peering Connectivity
  ✓ Found 1 VPC peering connections
  ✓ Discovered 2 VPC Peering connectivity paths (bi-directional)

[3/4] VPN Connectivity
  ✓ Found 1 VPN connections
  ✓ Discovered 1 VPN connectivity paths

[4/4] PrivateLink Connectivity
  ✓ Found 2 VPC endpoints
  ✓ Discovered 2 PrivateLink connectivity paths

TOTAL CONNECTIVITY PATHS DISCOVERED: 10

By Connection Type:
  TGW: 5
  PCX: 2
  VPN: 1
  VPCE: 2
```

### Step 2: Auto-Generate Tests Per Connection Type

```bash
python aft_test_orchestrator.py \
  --phase post-release \
  --golden-path golden_path.yaml
```

**Test Matrix (Auto-Generated):**

```
Generated 15 reachability tests:

TGW Tests (5):
1. ✓ vpc-prod-app → vpc-shared-services (Protocol-level)
2. ✓ vpc-prod-app → vpc-shared-services (TCP:443)
3. ✓ vpc-qa → vpc-shared-services (Protocol-level)
4. ✓ vpc-qa → vpc-shared-services (TCP:443)
5. ✓ vpc-prod-app → vpc-monitoring (TCP:9200)

Peering Tests (3):
6. ✓ vpc-prod-app → vpc-prod-backup (Protocol-level)
7. ✓ vpc-prod-app → vpc-prod-backup (TCP:3306) [Database backup]
8. ✓ vpc-prod-backup → vpc-prod-app (TCP:3306) [Bi-directional]

VPN Tests (1):
9. ✓ vpc-qa → on-premises (Tunnel Status)

PrivateLink Tests (2):
10. ✓ vpc-shared-services → S3 Endpoint (Status)
11. ✓ vpc-shared-services → DynamoDB Endpoint (Status)
```

---

## 🎯 Use Case: Database Backup via Peering

### Why Peering for Database Backup?

```
TGW Approach:
vpc-prod ──TGW──→ vpc-backup
  ❌ 50 Gbps bottleneck (TGW limit)
  ❌ $0.02/GB inter-AZ
  ❌ Additional hop latency

Peering Approach:
vpc-prod ──DIRECT──→ vpc-backup
  ✅ Full VPC bandwidth
  ✅ Free within same region
  ✅ Lowest latency
```

### Discovered Configuration

```yaml
# Automatically discovered from your infra:
- source_vpc: vpc-prod-app
  dest_vpc: vpc-prod-backup
  connection_type: pcx
  connection_id: pcx-abc123
  
  # Tagged with use case (from peering tags)
  use_case: database-backup
  
  # Observed traffic patterns
  traffic_observed: true
  protocols_observed: [tcp]
  ports_observed: [3306, 5432]  # MySQL, PostgreSQL
  packet_count: 1500000          # Heavy backup traffic
```

### Generated Tests

```python
# Test 1: Verify peering is active
test_peering_reachability(
    source_vpc="vpc-prod-app",
    dest_vpc="vpc-prod-backup",
    peering_id="pcx-abc123",
    protocol="-1"  # Protocol-level
)

# Test 2: Verify MySQL port reachable
test_peering_reachability(
    source_vpc="vpc-prod-app",
    dest_vpc="vpc-prod-backup",
    peering_id="pcx-abc123",
    protocol="tcp",
    port=3306  # Discovered from traffic!
)

# Test 3: Verify PostgreSQL port reachable
test_peering_reachability(
    source_vpc="vpc-prod-app",
    dest_vpc="vpc-prod-backup",
    peering_id="pcx-abc123",
    protocol="tcp",
    port=5432  # Discovered from traffic!
)
```

---

## 💡 Smart Features

### 1. Use Case Detection
```python
# Peering tagged with: UseCase=database-backup
# Automatically:
- Prioritizes testing during maintenance windows
- Tests bi-directional connectivity
- Validates database-specific ports (3306, 5432, etc.)
```

### 2. Connection-Specific Testing
```python
# TGW: Uses Reachability Analyzer with TGW attachments
test_tgw_reachability(...)

# Peering: Uses Reachability Analyzer with ENIs
test_peering_reachability(...)

# VPN: Checks tunnel status (Reachability Analyzer doesn't support VPN)
test_vpn_status(...)

# PrivateLink: Validates endpoint availability
test_endpoint_status(...)
```

### 3. Cost Awareness
```yaml
# Golden path includes cost info
connectivity:
  - connection_type: tgw
    cost_per_gb: 0.02
    use_for: general_traffic
  
  - connection_type: pcx
    cost_per_gb: 0.00  # Free in same region!
    use_for: high_bandwidth_backup
```

---

## 📋 Configuration

### Minimal Config Needed
```yaml
# accounts.yaml - just list accounts
accounts:
  - account_id: "111111111111"
    account_name: "prod-app"
    vpc_id: "vpc-abc123"
  
  - account_id: "222222222222"
    account_name: "prod-backup"
    vpc_id: "vpc-def456"

# That's it! Discovery finds:
# - TGW attachments automatically
# - Peering connections automatically
# - VPN connections automatically
# - PrivateLink endpoints automatically
```

### Auto-Generated Golden Path
```yaml
connectivity:
  by_connection_type:
    tgw: 5
    peering: 1
    vpn: 1
    privatelink: 1
  
  patterns:
    # All connections discovered automatically
    # Use cases identified from tags
    # Ports discovered from actual traffic
```

---

## 🔄 Real-World Workflow

### Day 1: Initial Discovery
```bash
# Discover everything
python aft_test_orchestrator.py --phase discover --tgw-id tgw-xyz789

# Output shows all connection types:
# ✓ 5 TGW paths
# ✓ 1 Peering path (database backup)
# ✓ 1 VPN path (on-premises)
# ✓ 1 PrivateLink path (S3)
```

### Daily: Run Tests
```bash
# Tests all connection types automatically
python aft_test_orchestrator.py --phase post-release

# Results:
# TGW Tests: 5/5 PASS ✓
# Peering Tests: 1/1 PASS ✓
# VPN Tests: 1/1 PASS ✓
# PrivateLink Tests: 1/1 PASS ✓
```

### When Adding New Connection
```
1. Create peering connection in AWS
2. Tag it: UseCase=backup
3. Run discovery
4. Tests auto-generated!
```

---

## ✅ Benefits of Multi-Connection Support

1. **Complete Visibility**
   - No blind spots - all connection types tested
   - Database backup paths validated
   - Hybrid connectivity monitored

2. **Use-Case Awareness**
   - Database backups tested with appropriate ports
   - General traffic vs. specialized paths
   - Cost-optimized routing validated

3. **Zero Manual Config**
   - Discovers all connection types automatically
   - Detects use cases from tags
   - Ports from actual traffic

4. **Production Ready**
   - Tests what you actually use
   - Validates critical backup paths
   - Monitors hybrid connectivity

---

## 🎓 Example: Complete Multi-Connection Setup

```yaml
# Discovered golden_path.yaml
connectivity:
  total_paths: 8
  
  patterns:
    # General traffic via TGW
    - source: vpc-prod
      dest: vpc-shared
      connection_type: tgw
      use_case: general
      ports: [443, 22]
    
    # Database backup via Peering (high bandwidth)
    - source: vpc-prod
      dest: vpc-backup
      connection_type: pcx
      use_case: database-backup
      ports: [3306, 5432]
      priority: high
    
    # On-premises access via VPN
    - source: vpc-qa
      dest: on-premises
      connection_type: vpn
      use_case: hybrid
    
    # AWS service access via PrivateLink
    - source: vpc-prod
      dest: s3-endpoint
      connection_type: vpce
      use_case: service-access
```

**This is exactly what you need for real-world AFT deployments!** 🎉

update auth logic, using SAML assertion
account not necessary specify vpc id
how to prevent NRA pathid duplicate