# AFTNetworkTestRole - Deploy to all AFT-vended accounts
# Copy this module to aft-global-customizations/terraform/

data "aws_caller_identity" "current" {}

resource "aws_iam_role" "network_test" {
  name = var.role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = [for account_id in var.trusted_account_ids : "arn:aws:iam::${account_id}:root"]
      }
      Action = "sts:AssumeRole"
      Condition = {
        StringLike = {
          "sts:RoleSessionName" = "aft-network-test-*"
        }
      }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "network_test" {
  name = "NetworkTestPolicy"
  role = aws_iam_role.network_test.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "NetworkReadOnly"
        Effect = "Allow"
        Action = [
          "ec2:Describe*",
          "ec2:GetTransitGateway*",
          "ec2:SearchTransitGateway*"
        ]
        Resource = "*"
      },
      {
        Sid    = "ReachabilityAnalyzer"
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInsightsPath",
          "ec2:DeleteNetworkInsightsPath",
          "ec2:StartNetworkInsightsAnalysis",
          "ec2:DescribeNetworkInsights*"
        ]
        Resource = "*"
      },
      {
        Sid    = "FlowLogsQuery"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:StartQuery",
          "logs:GetQueryResults"
        ]
        Resource = [
          "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/vpc/flowlogs/*",
          "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/vpc/flowlogs/*:*"
        ]
      }
    ]
  })
}

output "role_arn" {
  description = "ARN of the network test role"
  value       = aws_iam_role.network_test.arn
}

output "role_name" {
  description = "Name of the network test role"
  value       = aws_iam_role.network_test.name
}
