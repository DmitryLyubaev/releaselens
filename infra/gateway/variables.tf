variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
}

variable "location" {
  type        = string
  description = "Azure region of the gateway (spec §3.2)."
  default     = "australiaeast"
}

# The same account init's -backend-config names. The bootstrap's outputs are read from its
# state there, so no identifier is copied by hand.
variable "tfstate_storage_account" {
  type        = string
  description = "Name of the state storage account: terraform -chdir=../bootstrap output -raw tfstate_storage_account."
}

# API Management requires a publisher email. A variable, from the git-ignored tfvars, so no
# address is in the repository.
variable "publisher_email" {
  type        = string
  description = "Email address API Management sends its service notifications to."
}

# Low on purpose (spec §4.1): nothing real calls the gateway, and low limits make the budget
# checks quick and cheap. Every caller has the same limits and its own counter.
variable "tokens_per_minute" {
  type        = number
  description = "Each caller's token budget per minute. Over it, the gateway returns 429 with Retry-After."
  default     = 10000
}

variable "tokens_per_day" {
  type        = number
  description = "Each caller's token budget per day. Over it, the gateway returns 403."
  default     = 50000
}

# Revision 2 is made current only after it has been called at ;rev=2 in the smoke test
# (spec §3.2, §4.3).
variable "release_revision_2" {
  type        = bool
  description = "Make revision 2 of the API current. Set it to true only after revision 2 has passed the smoke test."
  default     = false
}
