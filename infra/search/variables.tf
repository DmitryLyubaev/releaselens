variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
}

variable "location" {
  type        = string
  description = "Azure region. The semantic ranker is offered in australiaeast (spec §6.3)."
  default     = "australiaeast"
}

# A variable, not data.azurerm_client_config, so the roles go to the owner whoever's sign-in
# applies the stack.
variable "owner_object_id" {
  type        = string
  description = "Entra object ID of the owner, who gets the two search roles: az ad signed-in-user show --query id -o tsv."

  validation {
    # A wrong value would otherwise fail only at apply.
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.owner_object_id))
    error_message = "owner_object_id must be an Entra object ID, a GUID."
  }
}

variable "tfstate_storage_account" {
  type        = string
  description = "Name of the storage account that holds every stack's state, to read the bootstrap's outputs from: terraform -chdir=../bootstrap output -raw tfstate_storage_account."
}
