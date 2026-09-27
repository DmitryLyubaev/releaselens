terraform {
  required_version = "~> 1.15.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.6"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id

  # Nothing is registered from here: CI may not register resource providers, azurerm 5.x
  # defaults resource_provider_registrations to none, and bootstrap registers the namespaces
  # this stack uses.
  features {}
}
