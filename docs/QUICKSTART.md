# Quick Start Guide

## 1. Installation

\`\`\`bash
git clone <repo>
cd aft-network-testing
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e .
\`\`\`

## 2. Configuration

\`\`\`bash
cp config/accounts.yaml.example config/accounts.yaml
\`\`\`

Edit `config/accounts.yaml` with your account details.

## 3. Discovery

\`\`\`bash
aft-test \\
  --mode local \\
  --profile aft-admin \\
  --phase discover \\
  --accounts-file config/accounts.yaml \\
  --tgw-id tgw-xyz789
\`\`\`

This generates `config/golden_path.yaml` with discovered connectivity.

## 4. Run Tests

\`\`\`bash
aft-test \\
  --mode local \\
  --profile aft-admin \\
  --phase post-release \\
  --accounts-file config/accounts.yaml \\
  --golden-path config/golden_path.yaml
\`\`\`

## 5. View Results

Results are published to:
- CloudWatch Metrics: `AFT/VPCTests` namespace
- S3: `s3://{bucket}/vpc-tests/`