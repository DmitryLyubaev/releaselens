terraform {
  required_version = "= 1.15.8"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "= 5.7.0" }
    random  = { source = "hashicorp/random", version = "= 3.9.1" }
  }
}
provider "azurerm" {
  subscription_id                 = var.subscription_id
  tenant_id                       = var.tenant_id
  resource_provider_registrations = "none"
  storage_use_azuread             = true
  features {}
}
