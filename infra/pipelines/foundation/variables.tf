variable "subscription_id" { type = string }
variable "tenant_id" { type = string }
variable "owner_object_id" { type = string }
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
variable "principals" {
  type = map(string)
  validation {
    condition     = toset(keys(var.principals)) == toset(["test", "prod", "verify"])
    error_message = "Supply exactly the three approved principal object IDs."
  }
}
