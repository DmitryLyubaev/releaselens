terraform {
  required_version = "~> 1.15.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.6"
    }
    # For what azurerm cannot yet express: the backend pool (PR #31144) and the diagnostic's
    # metrics switch (PR #28499).
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.13"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id

  # Bootstrap registers Microsoft.ApiManagement, with every other namespace the stacks use, so
  # nothing is registered from here. "none" is written out, as in the other stacks, rather than
  # left to the provider's default.
  resource_provider_registrations = "none"

  features {
    # A destroy purges the soft-deleted service, so no deleted instance is left behind, and a
    # create never brings an old one back with its old configuration (spec §3.2).
    api_management {
      purge_soft_delete_on_destroy = true
      recover_soft_deleted         = false
    }
  }
}

# The same sign-in as azurerm: the owner's az session, in the same subscription.
provider "azapi" {
  subscription_id = var.subscription_id
}
