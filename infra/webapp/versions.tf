terraform {
  required_version = "~> 1.15.0"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 5.6" }
    azapi   = { source = "Azure/azapi", version = "~> 2.13" }
  }
}
provider "azurerm" {
  subscription_id                 = var.subscription_id
  resource_provider_registrations = "none"
  features {}
}
provider "azapi" {
  subscription_id = var.subscription_id
}
