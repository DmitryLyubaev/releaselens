variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
}

variable "location" {
  type        = string
  description = "Azure region of the apps (spec §3.3)."
  default     = "australiaeast"
}

# The same account init's -backend-config names. The bootstrap's, the search stack's and the
# gateway's outputs are read from their states there, so no identifier is copied by hand.
variable "tfstate_storage_account" {
  type        = string
  description = "Name of the state storage account: terraform -chdir=../bootstrap output -raw tfstate_storage_account."
}
