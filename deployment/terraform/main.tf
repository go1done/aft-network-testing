terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Lambda function
resource "aws_lambda_function" "aft_network_test" {
  filename      = "lambda_deployment.zip"
  function_name = "aft-network-test"
  role          = aws_iam_role.aft_test_lambda.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.11"
  timeout       = 900
  memory_size   = 512

  environment {
    variables = {
      AFT_ROLE_NAME     = var.aft_execution_role
      S3_RESULTS_BUCKET = aws_s3_bucket.test_results.bucket
    }
  }
}

# S3 bucket for results
resource "aws_s3_bucket" "test_results" {
  bucket = var.results_bucket_name
}

resource "aws_s3_bucket_lifecycle_configuration" "test_results" {
  bucket = aws_s3_bucket.test_results.id

  rule {
    id     = "expire-old-results"
    status = "Enabled"

    expiration {
      days = 90
    }
  }
}

# EventBridge for scheduling
resource "aws_cloudwatch_event_rule" "daily_test" {
  name                = "aft-daily-network-test"
  description         = "Run AFT network tests daily"
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.daily_test.name
  target_id = "AFTNetworkTestLambda"
  arn       = aws_lambda_function.aft_network_test.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.aft_network_test.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_test.arn
}