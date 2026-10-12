variable "subscription_id" {
  type        = string
  description = "Expected owner subscription, supplied privately."
}
variable "location" {
  type        = string
  default     = "australiaeast"
  description = "Session region."
  validation {
    condition     = var.location == "australiaeast"
    error_message = "The approved session region is Australia East."
  }
}
variable "tfstate_storage_account" {
  type        = string
  description = "Existing state account, supplied privately."
}
