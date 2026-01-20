# AFTNetworkTestRole - Target Account Role

This module creates the `AFTNetworkTestRole` IAM role in AFT-vended accounts, enabling the network testing framework to perform reachability analysis.

## Permissions

The role grants minimal permissions for network testing:

| Permission | Purpose |
|------------|---------|
| `ec2:Describe*` | Read VPC, subnet, TGW, security group configs |
| `ec2:*NetworkInsights*` | Create/run Reachability Analyzer paths |
| `ec2:*TransitGateway*` | Query TGW routes and attachments |
| `logs:StartQuery`, `logs:GetQueryResults` | Query VPC Flow Logs |

## Deployment via AFT Global Customizations

### 1. Copy to AFT Global Customizations

```bash
cp -r target-account-role/* /path/to/aft-global-customizations/terraform/
```

### 2. Update aft-global-customizations/terraform/main.tf

```hcl
module "network_test_role" {
  source = "./target-account-role"

  trusted_account_ids = [
    "111111111111"  # AFT Management Account ID
  ]

  tags = {
    ManagedBy = "AFT"
    Purpose   = "NetworkTesting"
  }
}
```

Or directly in the root:

```hcl
# aft-global-customizations/terraform/main.tf

variable "aft_management_account_id" {
  description = "AFT Management Account ID"
  type        = string
}

resource "aws_iam_role" "network_test" {
  name = "AFTNetworkTestRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = "arn:aws:iam::${var.aft_management_account_id}:root"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "network_test" {
  name = "NetworkTestPolicy"
  role = aws_iam_role.network_test.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ec2:Describe*", "ec2:GetTransitGateway*", "ec2:SearchTransitGateway*"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ec2:CreateNetworkInsightsPath", "ec2:DeleteNetworkInsightsPath", "ec2:StartNetworkInsightsAnalysis", "ec2:DescribeNetworkInsights*"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups", "logs:StartQuery", "logs:GetQueryResults"]
        Resource = "arn:aws:logs:*:*:log-group:/aws/vpc/flowlogs/*"
      }
    ]
  })
}
```

### 3. Set Variable in aft-global-customizations/terraform/terraform.tfvars

```hcl
aft_management_account_id = "111111111111"
```

### 4. Commit and Push

AFT will automatically deploy this role to all vended accounts.

## Usage

Once deployed, the network testing framework can assume this role:

```python
from auth import AuthConfig
from models import ExecutionMode

# From AFT Management Account (Lambda/CodeBuild)
auth = AuthConfig(
    mode=ExecutionMode.AWS_CODEBUILD,
    role_name="AFTNetworkTestRole"
)

# Get session for target account
session = auth.get_account_session("222222222222")
```
