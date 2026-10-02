locals {
  azure_openai_account_name = "aoai-releaselens-${random_string.suffix.result}"
  azure_openai_base_url     = "https://${azurerm_cognitive_account.openai.custom_subdomain_name}.openai.azure.com/openai/v1/"
}

# Kind AIServices, not OpenAI: Microsoft upgrades eligible long-lived OpenAI-kind accounts to
# AIServices, and azurerm cannot set the opt-out, so a later plan would try to roll the kind
# back (spec §4.9).
resource "azurerm_cognitive_account" "openai" {
  name                = local.azure_openai_account_name
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location
  kind                = "AIServices"
  sku_name            = "S0"

  # Entra ID authentication needs a custom subdomain, and changing it replaces the account,
  # so it comes from the suffix, which is generated once.
  custom_subdomain_name = local.azure_openai_account_name

  # With key authentication off, every call has to be authorised through Entra ID. azurerm
  # defaults this to true.
  local_auth_enabled = false
  # azurerm defaults this to false. It is written out because, once it is on, turning it off
  # again replaces the account.
  project_management_enabled = false
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.azure_openai_deployment_name
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }

  # Global Standard, not regional Standard: the subscription's regional quota for this model
  # is 0 (spec §4.8).
  sku {
    name     = "GlobalStandard"
    capacity = var.azure_openai_capacity
  }

  # azurerm defaults to OnceNewDefaultVersionAvailable, under which Azure moves the deployment
  # to a new model version by itself. With the pin, the deployment instead stops working when
  # 2025-04-14 retires (spec §4.8).
  version_upgrade_option = "NoAutoUpgrade"
}

# The retrieval benchmark's two embedding models (spec §6.1). Like the chat deployment, they
# bill per token and cost nothing idle, and their versions are pinned so a run is repeatable.
# Capacity 350 is 350,000 tokens a minute, within the subscription's Global Standard quota of
# 1,000 for each model.
resource "azurerm_cognitive_deployment" "embedding_small" {
  name                 = "releaselens-embed-small"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "text-embedding-3-small"
    version = "1"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 350
  }

  version_upgrade_option = "NoAutoUpgrade"
}

resource "azurerm_cognitive_deployment" "embedding_large" {
  name                 = "releaselens-embed-large"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "text-embedding-3-large"
    version = "1"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 350
  }

  version_upgrade_option = "NoAutoUpgrade"
}
