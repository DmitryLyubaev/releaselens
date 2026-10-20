variable "subscription_id" { type = string }
variable "tenant_id" { type = string }
variable "owner_object_id" { type = string }
variable "location" {
  type    = string
  default = "australiaeast"
}
variable "federation" {
  type = map(object({ issuer = string, subject = string }))
  validation {
    condition     = toset(keys(var.federation)) == toset(["test", "prod", "verify"]) && alltrue([for item in values(var.federation) : startswith(item.issuer, "https://") && length(item.subject) > 5])
    error_message = "Supply the current issuer and subject of exactly the three approved connections."
  }
}
