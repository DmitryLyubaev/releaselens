terraform {
  required_version = "~> 1.15.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.6"
    }
    # For what azurerm cannot express without breaking the design: the Flex apps (azurerm's
    # resource injects an AzureWebJobsStorage connection string, #29693, #33211, #30732), their
    # authentication settings, and everything on the gateway at 2025-09-01-preview (the MCP API
    # type has no azurerm resource).
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

  # Bootstrap registers Microsoft.Web, with every other namespace the stacks use, so nothing is
  # registered from here. "none" is written out, as in the other stacks, rather than left to the
  # provider's default.
  resource_provider_registrations = "none"

  features {}
}

# The same sign-in as azurerm: the owner's az session, in the same subscription.
provider "azapi" {
  subscription_id = var.subscription_id
}
