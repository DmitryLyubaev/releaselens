variable "subscription_id" { type = string }
variable "tenant_id" { type = string }
variable "search_host" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]+[a-z0-9][.]search[.]windows[.]net$", var.search_host))
    error_message = "Pass the approved bare Search hostname, never an ARM ID or URL."
  }
}
variable "target" {
  type = string
  validation {
    condition     = contains(["test", "demo-prod"], var.target)
    error_message = "Only the two approved index targets are allowed."
  }
}
variable "schema_file" { type = string }
