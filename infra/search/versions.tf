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

  # Bootstrap registers Microsoft.Search, with every other namespace the stacks use, so nothing
  # is registered from here. "none" is written out, as in the other stacks, rather than left to
  # the provider's default.
  resource_provider_registrations = "none"

  features {}
}
