terraform {
  required_version = "~> 1.15.0"
  backend "local" {}
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 5.6" }
  }
}

provider "azurerm" {
  subscription_id                 = var.subscription_id
  tenant_id                       = var.tenant_id
  resource_provider_registrations = "none"
  features {
    key_vault {
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = false
    }
  }
}
