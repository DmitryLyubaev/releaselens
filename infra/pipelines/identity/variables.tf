variable "subscription_id" { type = string }
variable "tenant_id" { type = string }
variable "location" {
  type    = string
  default = "australiaeast"
}
variable "session_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{3,11}$", var.session_name))
    error_message = "Use a fresh 4-12 character session slug."
  }
}
variable "federation" {
  type = map(object({ issuer = string, subject = string }))
  validation {
    condition     = toset(keys(var.federation)) == toset(["test", "prod", "verify"]) && alltrue([for item in values(var.federation) : startswith(item.issuer, "https://") && length(item.subject) > 5])
    error_message = "Supply actual current issuer/subject for exactly the three approved connections."
  }
}
