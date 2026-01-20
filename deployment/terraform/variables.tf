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
  description = "IAM role name to assume in AFT accounts"
  type        = string
  default     = "AWSAFTExecution"
}

variable "schedule_expression" {
  description = "CloudWatch Events schedule expression"
  type        = string
  default     = "cron(0 6 * * ? *)"
}