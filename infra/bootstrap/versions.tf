terraform {
  required_version = "~> 1.15.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.6"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# The owner's az sign-in, in the tenant the azurerm provider signs in to. Applying this stack
# needs the right to create app registrations in that tenant.
provider "azuread" {
  tenant_id = data.azurerm_client_config.current.tenant_id
}

provider "azurerm" {
  subscription_id = var.subscription_id

  # Registration happens here, for every stack: this stack runs as the owner, who may
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
    "Microsoft.Search",
    "Microsoft.ApiManagement",
    "Microsoft.OperationalInsights",
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
