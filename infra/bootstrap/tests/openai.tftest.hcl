variables {
  subscription_id    = "00000000-0000-0000-0000-000000000000"
  budget_alert_email = "owner@example.com"
  budget_start_date  = "2026-10-01T00:00:00Z"
}

mock_provider "azurerm" {
  override_during = plan

  mock_resource "azurerm_cognitive_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.CognitiveServices/accounts/aoai-releaselens-a1b2c3"
    }
  }
}

# The account's name, its subdomain and the base URL are built from the suffix, so the
# suffix must be known at plan.
mock_provider "random" {
  override_during = plan

  mock_resource "random_string" {
    defaults = {
      result = "a1b2c3"
    }
  }
}

run "account_and_deployment" {
  command = plan

  assert {
    condition     = azurerm_cognitive_account.openai.kind == "AIServices"
    error_message = "The account's kind must be AIServices."
  }

  assert {
    condition     = azurerm_cognitive_account.openai.local_auth_enabled == false
    error_message = "The account's key authentication must be off."
  }

  assert {
    condition     = azurerm_cognitive_account.openai.project_management_enabled == false
    error_message = "The account's project management must be off."
  }

  assert {
    condition     = startswith(azurerm_cognitive_account.openai.custom_subdomain_name, "aoai-releaselens-")
    error_message = "The account's custom subdomain must start with aoai-releaselens-."
  }

  assert {
    condition     = azurerm_cognitive_account.openai.name == azurerm_cognitive_account.openai.custom_subdomain_name
    error_message = "The account's name and its custom subdomain must be the same."
  }

  assert {
    condition     = azurerm_cognitive_account.openai.resource_group_name == "rg-releaselens-bootstrap" && azurerm_cognitive_account.openai.location == "australiaeast"
    error_message = "The account must be in the bootstrap group, in australiaeast."
  }

  assert {
    condition     = azurerm_cognitive_account.openai.sku_name == "S0"
    error_message = "The account's SKU must be S0."
  }

  assert {
    condition     = azurerm_cognitive_deployment.chat.name == "releaselens-chat"
    error_message = "The deployment must be named releaselens-chat by default."
  }

  assert {
    condition     = azurerm_cognitive_deployment.chat.cognitive_account_id == azurerm_cognitive_account.openai.id
    error_message = "The deployment must be on the account."
  }

  assert {
    condition     = azurerm_cognitive_deployment.chat.model[0].format == "OpenAI" && azurerm_cognitive_deployment.chat.model[0].name == "gpt-4.1-mini" && azurerm_cognitive_deployment.chat.model[0].version == "2025-04-14"
    error_message = "The deployment's model must be OpenAI gpt-4.1-mini, version 2025-04-14."
  }

  assert {
    condition     = azurerm_cognitive_deployment.chat.sku[0].name == "GlobalStandard"
    error_message = "The deployment's SKU must be GlobalStandard."
  }

  assert {
    condition     = azurerm_cognitive_deployment.chat.sku[0].capacity == 300
    error_message = "The deployment's capacity must be 300 by default."
  }

  assert {
    condition     = azurerm_cognitive_deployment.chat.version_upgrade_option == "NoAutoUpgrade"
    error_message = "The deployment's model version must be pinned with NoAutoUpgrade."
  }

  assert {
    condition     = startswith(local.azure_openai_base_url, "https://aoai-releaselens-") && endswith(local.azure_openai_base_url, ".openai.azure.com/openai/v1/")
    error_message = "The base URL must be the account's v1 endpoint: https://aoai-releaselens-<suffix>.openai.azure.com/openai/v1/."
  }
}

run "capacity_from_variable" {
  command = plan

  variables {
    azure_openai_capacity = 250
  }

  assert {
    condition     = azurerm_cognitive_deployment.chat.sku[0].capacity == 250
    error_message = "The deployment's capacity must come from azure_openai_capacity."
  }
}
