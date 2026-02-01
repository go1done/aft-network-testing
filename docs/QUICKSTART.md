# Quick Start Guide

## 1. Installation

```bash
git clone <repo>
cd aft-network-testing
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## 2. Configuration

```bash
cp config/accounts.yaml.example config/accounts.yaml
```

Edit `config/accounts.yaml`:

```yaml
accounts:
  - account_id: "111111111111"
    account_name: "network-hub"
    region: "us-east-1"
    # vpc_id: optional, auto-discovered

  - account_id: "222222222222"
    account_name: "prod-app"
    region: "us-east-1"
```

## 3. Discovery

### Single SSO Profile (recommended)

```bash
aft-test \
  --mode local \
  --profile aft-admin \
  --phase discover \
  --accounts-file config/accounts.yaml
```

### Per-Account Profiles

```bash
aft-test \
  --mode local \
  --profile-pattern "{account_id}" \
  --phase discover \
  --accounts-file config/accounts.yaml
```

### Discover Specific Connection Types

```bash
aft-test \
  --mode local \
  --profile aft-admin \
  --phase discover \
  --accounts-file config/accounts.yaml \
  --connection-types tgw,peering
```

This generates `golden_path.yaml` with discovered connectivity.

## 4. Run Tests

```bash
aft-test \
  --mode local \
  --profile aft-admin \
  --phase post-release \
  --accounts-file config/accounts.yaml \
  --golden-path golden_path.yaml
```

## 5. Publish Results (Optional)

By default, results are only printed to console. To publish:

```bash
# CloudWatch only
aft-test \
  --mode local \
  --profile aft-admin \
  --phase post-release \
  --accounts-file config/accounts.yaml \
  --golden-path golden_path.yaml \
  --publish-results

# CloudWatch + S3
aft-test \
  --mode local \
  --profile aft-admin \
  --phase post-release \
  --accounts-file config/accounts.yaml \
  --golden-path golden_path.yaml \
  --publish-results \
  --s3-bucket my-results-bucket
```

Results published to:
- CloudWatch Metrics: `AFT/VPCTests` namespace
- S3: `s3://{bucket}/vpc-tests/{phase}/{timestamp}.json`

## 6. Validate Configuration (Dry Run)

```bash
aft-test \
  --dry-run \
  --phase discover \
  --accounts-file config/accounts.yaml
```

## Test Phases

| Phase | Use Case |
|-------|----------|
| `discover` | Generate golden path from current infrastructure |
| `pre-release` | Validate current state before Terraform apply |
| `post-release` | Verify connectivity after Terraform apply |

## Next Steps

- See [DESIGN.md](DESIGN.md) for architecture details
- See [solution.md](solution.md) for multi-connection type examples
