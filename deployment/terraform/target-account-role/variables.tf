variable "role_name" {
  description = "Name of the IAM role for network testing"
  type        = string
  default     = "AFTNetworkTestRole"
}

variable "trusted_account_ids" {
  description = "List of AWS account IDs allowed to assume this role (e.g., AFT management account)"
  type        = list(string)
}

variable "tags" {
  description = "Tags to apply to the role"
  type        = map(string)
  default = {
    ManagedBy = "AFT"
    Purpose   = "NetworkTesting"
  }
}
