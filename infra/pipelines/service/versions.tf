terraform {
  required_version = "= 1.15.8"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "= 5.7.0" }
  }
}
provider "azurerm" {
  subscription_id                 = var.subscription_id
  tenant_id                       = var.tenant_id
  resource_provider_registrations = "none"
  features {}
}
