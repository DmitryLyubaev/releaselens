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

  # Registration happens here, for both stacks: this stack runs as the owner, who may
  # register resource providers, and the app stack's CI identity may not. "none" is
  # written out so that only this list is registered, whatever the provider's default.
  resource_provider_registrations = "none"
  resource_providers_to_register = [
    "Microsoft.Storage",
    "Microsoft.ManagedIdentity",
    "Microsoft.CognitiveServices",
    "Microsoft.App",
    "Microsoft.DBforPostgreSQL",
    "Microsoft.Consumption",
    "Microsoft.Insights",
  ]

  # Storage data-plane calls authenticate with Entra ID instead of account keys, so they
  # keep working on an account whose shared keys are off.
  storage_use_azuread = true

  features {
    cognitive_account {
      purge_soft_delete_on_destroy = true
    }
  }
}
