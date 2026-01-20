variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "results_bucket_name" {
  description = "S3 bucket name for test results"
  type        = string
}

variable "aft_execution_role" {
  description = "IAM role name to assume in target accounts for network testing"
  type        = string
  default     = "AFTNetworkTestRole"
}

variable "schedule_expression" {
  description = "CloudWatch Events schedule expression"
  type        = string
  default     = "cron(0 6 * * ? *)"
}