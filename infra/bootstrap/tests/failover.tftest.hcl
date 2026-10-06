variables {
  subscription_id    = "00000000-0000-0000-0000-000000000000"
  budget_alert_email = "owner@example.com"
  budget_start_date  = "2026-10-01T00:00:00Z"
}

# The gateway's Entra app is part of the stack, so every test plans it. The data source's result
# needs the Azure CLI's key.
mock_provider "azuread" {
  mock_data "azuread_application_published_app_ids" {
    defaults = {
      result = {
        MicrosoftAzureCli = "99999999-9999-9999-9999-999999999999"
      }
    }
  }
}

# The two accounts get distinct IDs, so a deployment placed on the wrong account fails its
# assertion instead of passing on a shared mock value. The mock default is the australiaeast
# account's; the override gives the Southeast Asia account its own.
mock_provider "azurerm" {
  override_during = plan

  mock_resource "azurerm_cognitive_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.CognitiveServices/accounts/aoai-releaselens-a1b2c3"
    }
  }

  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-deploy"
      principal_id = "33333333-3333-3333-3333-333333333333"
      client_id    = "44444444-4444-4444-4444-444444444444"
    }
  }
}

# The account's name, its subdomain and the backend URLs are built from the suffix, so the
# suffix must be known at plan.
mock_provider "random" {
  override_during = plan

  mock_resource "random_string" {
    defaults = {
      result = "a1b2c3"
    }
  }
}

override_resource {
  target          = azurerm_cognitive_account.failover
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.CognitiveServices/accounts/aoai-releaselens-sea-a1b2c3"
  }
}

override_resource {
  target          = azurerm_user_assigned_identity.gateway
  override_during = plan
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-gateway"
    principal_id = "77777777-7777-7777-7777-777777777777"
    client_id    = "88888888-8888-8888-8888-888888888888"
  }
}

# The gateway app's owner is the signed-in principal, and the provider checks that it is a UUID.
override_data {
  target = data.azurerm_client_config.current
  values = {
    object_id = "22222222-2222-2222-2222-222222222222"
    tenant_id = "11111111-1111-1111-1111-111111111111"
  }
}

run "failover_account" {
  command = plan

  assert {
    condition     = azurerm_cognitive_account.failover.kind == "AIServices" && azurerm_cognitive_account.failover.sku_name == "S0"
    error_message = "The failover account must be kind AIServices, SKU S0."
  }

  assert {
    condition     = azurerm_cognitive_account.failover.local_auth_enabled == false
    error_message = "The failover account's key authentication must be off."
  }

  assert {
    condition     = azurerm_cognitive_account.failover.project_management_enabled == false
    error_message = "The failover account's project management must be off."
  }

  assert {
    condition     = azurerm_cognitive_account.failover.location == "southeastasia"
    error_message = "The failover account must be in southeastasia by default."
  }

  assert {
    condition     = azurerm_cognitive_account.failover.name == "aoai-releaselens-sea-a1b2c3" && azurerm_cognitive_account.failover.custom_subdomain_name == "aoai-releaselens-sea-a1b2c3"
    error_message = "The failover account's name and custom subdomain must both be aoai-releaselens-sea-<suffix>."
  }

  assert {
    condition     = azurerm_cognitive_account.failover.resource_group_name == "rg-releaselens-bootstrap"
    error_message = "The failover account must be in the bootstrap group."
  }
}

run "failover_location_from_variable" {
  command = plan

  variables {
    failover_location = "japaneast"
  }

  assert {
    condition     = azurerm_cognitive_account.failover.location == "japaneast"
    error_message = "The failover account's location must come from failover_location."
  }
}

run "failover_deployments" {
  command = plan

  assert {
    condition     = azurerm_cognitive_deployment.failover_chat.name == "releaselens-chat" && azurerm_cognitive_deployment.failover_chat.cognitive_account_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.CognitiveServices/accounts/aoai-releaselens-sea-a1b2c3"
    error_message = "releaselens-chat must be a deployment on the Southeast Asia account."
  }

  assert {
    condition     = azurerm_cognitive_deployment.failover_test.name == "releaselens-chat-failover-test" && azurerm_cognitive_deployment.failover_test.cognitive_account_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.CognitiveServices/accounts/aoai-releaselens-sea-a1b2c3"
    error_message = "releaselens-chat-failover-test must be a deployment on the Southeast Asia account."
  }

  assert {
    condition     = azurerm_cognitive_deployment.primary_failover_test.name == "releaselens-chat-failover-test" && azurerm_cognitive_deployment.primary_failover_test.cognitive_account_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.CognitiveServices/accounts/aoai-releaselens-a1b2c3"
    error_message = "The tiny releaselens-chat-failover-test must be a deployment on the australiaeast account."
  }

  assert {
    condition = alltrue([
      for d in [
        azurerm_cognitive_deployment.failover_chat,
        azurerm_cognitive_deployment.failover_test,
        azurerm_cognitive_deployment.primary_failover_test,
      ] : d.model[0].format == "OpenAI" && d.model[0].name == "gpt-4.1-mini" && d.model[0].version == "2025-04-14"
    ])
    error_message = "All three deployments must be OpenAI gpt-4.1-mini, version 2025-04-14."
  }

  assert {
    condition = alltrue([
      for d in [
        azurerm_cognitive_deployment.failover_chat,
        azurerm_cognitive_deployment.failover_test,
        azurerm_cognitive_deployment.primary_failover_test,
      ] : d.sku[0].name == "GlobalStandard" && d.version_upgrade_option == "NoAutoUpgrade"
    ])
    error_message = "All three deployments must be GlobalStandard and pinned with NoAutoUpgrade."
  }

  assert {
    condition     = azurerm_cognitive_deployment.primary_failover_test.sku[0].capacity == 1
    error_message = "The australiaeast failover-test deployment must have capacity 1 (1,000 tokens a minute), so that it throttles on purpose."
  }

  assert {
    condition     = azurerm_cognitive_deployment.failover_chat.sku[0].capacity == 100 && azurerm_cognitive_deployment.failover_test.sku[0].capacity == 100
    error_message = "Both Southeast Asia deployments must have capacity 100 by default."
  }
}

run "failover_capacity_from_variable" {
  command = plan

  variables {
    failover_capacity = 40
  }

  assert {
    condition     = azurerm_cognitive_deployment.failover_chat.sku[0].capacity == 40 && azurerm_cognitive_deployment.failover_test.sku[0].capacity == 40
    error_message = "The Southeast Asia deployments' capacity must come from failover_capacity."
  }

  assert {
    condition     = azurerm_cognitive_deployment.primary_failover_test.sku[0].capacity == 1
    error_message = "failover_capacity must not change the australiaeast failover-test deployment's capacity of 1."
  }
}

run "gateway_identity_and_handoff" {
  command = plan

  assert {
    condition     = azurerm_user_assigned_identity.gateway.name == "id-releaselens-gateway" && azurerm_user_assigned_identity.gateway.resource_group_name == "rg-releaselens-bootstrap" && azurerm_user_assigned_identity.gateway.location == "australiaeast"
    error_message = "The gateway identity must be id-releaselens-gateway, in the bootstrap group, in australiaeast."
  }

  assert {
    condition     = output.gateway_identity_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-gateway" && output.gateway_identity_client_id == "88888888-8888-8888-8888-888888888888"
    error_message = "The gateway identity's outputs must be its resource ID and its client ID."
  }

  assert {
    condition     = output.primary_openai_backend_url == "https://aoai-releaselens-a1b2c3.openai.azure.com/openai/v1" && output.failover_openai_backend_url == "https://aoai-releaselens-sea-a1b2c3.openai.azure.com/openai/v1"
    error_message = "The backend URLs must be each account's v1 endpoint, with no trailing slash."
  }

  assert {
    condition     = output.failover_test_deployment == "releaselens-chat-failover-test"
    error_message = "failover_test_deployment must name the failover-test deployment."
  }
}
