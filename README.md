# AFT Network Testing Framework

Comprehensive network testing for AWS Control Tower AFT environments.

## Features

- ✅ Multi-connection type discovery (TGW, Peering, VPN, PrivateLink)
- ✅ Automatic baseline generation
- ✅ Golden path compliance validation
- ✅ AWS native testing (Reachability Analyzer)
- ✅ CloudWatch metrics and S3 results
- ✅ Local and AWS execution modes

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