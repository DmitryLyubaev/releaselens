variable "subscription_id" { type = string }
variable "tenant_id" { type = string }
variable "search_service_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$", var.search_service_name))
    error_message = "Pass the configured bare search service name."
  }
}
variable "resource_group_name" { type = string }
variable "location" { type = string }
