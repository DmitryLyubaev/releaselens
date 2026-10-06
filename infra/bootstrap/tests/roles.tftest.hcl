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

# Every principal, client ID and scope below is distinct, so an assignment given the wrong
# principal or scope fails its assertion instead of passing on a shared mock value. The mock
# defaults give the bootstrap group, the bootstrap container and the deploy identity their
# values; the override_resource blocks give the other instance of each type its own.
mock_provider "azurerm" {
  override_during = plan

  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap"
    }
  }

  mock_resource "azurerm_storage_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3"
    }
  }

  # url is given its real data-plane form. It passes the provider's scope validation, so only
  # the scope assertions catch a role scoped to it, which would fail at apply.
  mock_resource "azurerm_storage_container" {
    defaults = {
      id  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3/blobServices/default/containers/tfstate-bootstrap"
      url = "https://strlstatea1b2c3.blob.core.windows.net/tfstate-bootstrap"
    }
  }

  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-deploy"
      principal_id = "33333333-3333-3333-3333-333333333333"
      client_id    = "44444444-4444-4444-4444-444444444444"
    }
  }

  mock_resource "azurerm_cognitive_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.CognitiveServices/accounts/aoai-releaselens-a1b2c3"
    }
  }

  mock_resource "azurerm_application_insights" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Insights/components/appi-releaselens"
    }
  }
}

# The storage account's name and the base URL are built from the suffix, so the suffix must
# be known at plan.
mock_provider "random" {
  override_during = plan

  mock_resource "random_string" {
    defaults = {
      result = "a1b2c3"
    }
  }
}

override_resource {
  target          = azurerm_resource_group.app
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens"
  }
}

override_resource {
  target          = azurerm_storage_container.app
  override_during = plan
  values = {
    id  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3/blobServices/default/containers/tfstate-app"
    url = "https://strlstatea1b2c3.blob.core.windows.net/tfstate-app"
  }
}

override_resource {
  target          = azurerm_storage_container.search
  override_during = plan
  values = {
    id  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3/blobServices/default/containers/tfstate-search"
    url = "https://strlstatea1b2c3.blob.core.windows.net/tfstate-search"
  }
}

override_resource {
  target          = azurerm_storage_container.gateway
  override_during = plan
  values = {
    id  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3/blobServices/default/containers/tfstate-gateway"
    url = "https://strlstatea1b2c3.blob.core.windows.net/tfstate-gateway"
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

override_resource {
  target          = azurerm_user_assigned_identity.app
  override_during = plan
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-app"
    principal_id = "55555555-5555-5555-5555-555555555555"
    client_id    = "66666666-6666-6666-6666-666666666666"
  }
}

# The owner is whoever applies the stack: the signed-in principal.
override_data {
  target = data.azurerm_client_config.current
  values = {
    object_id = "22222222-2222-2222-2222-222222222222"
    tenant_id = "11111111-1111-1111-1111-111111111111"
  }
}

run "role_assignments" {
  command = plan

  assert {
    condition     = azurerm_role_assignment.app_openai_user.role_definition_name == "Cognitive Services OpenAI User" && azurerm_role_assignment.app_openai_user.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.CognitiveServices/accounts/aoai-releaselens-a1b2c3" && azurerm_role_assignment.app_openai_user.principal_id == "55555555-5555-5555-5555-555555555555"
    error_message = "app_openai_user must give the app identity Cognitive Services OpenAI User on the account."
  }

  assert {
    condition     = azurerm_role_assignment.owner_openai_user.role_definition_name == "Cognitive Services OpenAI User" && azurerm_role_assignment.owner_openai_user.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.CognitiveServices/accounts/aoai-releaselens-a1b2c3" && azurerm_role_assignment.owner_openai_user.principal_id == "22222222-2222-2222-2222-222222222222"
    error_message = "owner_openai_user must give the owner Cognitive Services OpenAI User on the account."
  }

  assert {
    condition     = azurerm_role_assignment.owner_state_bootstrap.role_definition_name == "Storage Blob Data Contributor" && azurerm_role_assignment.owner_state_bootstrap.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3/blobServices/default/containers/tfstate-bootstrap" && azurerm_role_assignment.owner_state_bootstrap.principal_id == "22222222-2222-2222-2222-222222222222"
    error_message = "owner_state_bootstrap must give the owner Storage Blob Data Contributor on the tfstate-bootstrap container's Resource Manager ID."
  }

  assert {
    condition     = azurerm_role_assignment.owner_state_app.role_definition_name == "Storage Blob Data Contributor" && azurerm_role_assignment.owner_state_app.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3/blobServices/default/containers/tfstate-app" && azurerm_role_assignment.owner_state_app.principal_id == "22222222-2222-2222-2222-222222222222"
    error_message = "owner_state_app must give the owner Storage Blob Data Contributor on the tfstate-app container's Resource Manager ID."
  }

  assert {
    condition     = azurerm_role_assignment.owner_state_search.role_definition_name == "Storage Blob Data Contributor" && azurerm_role_assignment.owner_state_search.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3/blobServices/default/containers/tfstate-search" && azurerm_role_assignment.owner_state_search.principal_id == "22222222-2222-2222-2222-222222222222"
    error_message = "owner_state_search must give the owner Storage Blob Data Contributor on the tfstate-search container's Resource Manager ID."
  }

  assert {
    condition     = azurerm_role_assignment.deploy_contributor.role_definition_name == "Contributor" && azurerm_role_assignment.deploy_contributor.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens" && azurerm_role_assignment.deploy_contributor.principal_id == "33333333-3333-3333-3333-333333333333"
    error_message = "deploy_contributor must give the deploy identity Contributor on rg-releaselens only."
  }

  assert {
    condition     = azurerm_role_assignment.deploy_identity_operator.role_definition_name == "Managed Identity Operator" && azurerm_role_assignment.deploy_identity_operator.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-app" && azurerm_role_assignment.deploy_identity_operator.principal_id == "33333333-3333-3333-3333-333333333333"
    error_message = "deploy_identity_operator must give the deploy identity Managed Identity Operator on the app identity only."
  }

  assert {
    condition     = azurerm_role_assignment.deploy_state_app.role_definition_name == "Storage Blob Data Contributor" && azurerm_role_assignment.deploy_state_app.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3/blobServices/default/containers/tfstate-app" && azurerm_role_assignment.deploy_state_app.principal_id == "33333333-3333-3333-3333-333333333333"
    error_message = "deploy_state_app must give the deploy identity Storage Blob Data Contributor on the tfstate-app container's Resource Manager ID only."
  }

  assert {
    condition = alltrue([
      for a in [
        azurerm_role_assignment.app_openai_user,
        azurerm_role_assignment.owner_openai_user,
        azurerm_role_assignment.owner_state_bootstrap,
        azurerm_role_assignment.owner_state_app,
        azurerm_role_assignment.owner_state_search,
        azurerm_role_assignment.deploy_contributor,
        azurerm_role_assignment.deploy_identity_operator,
        azurerm_role_assignment.deploy_state_app,
        azurerm_role_assignment.gateway_openai_user_primary,
        azurerm_role_assignment.gateway_openai_user_failover,
        azurerm_role_assignment.owner_state_gateway,
        azurerm_role_assignment.gateway_metrics_publisher,
      ] : lower(trimsuffix(a.scope, "/")) != lower("/subscriptions/${var.subscription_id}")
    ])
    error_message = "No role assignment may be scoped to the subscription."
  }

  # Stricter than the check above: it also rejects the root scope and management groups.
  assert {
    condition = alltrue([
      for a in [
        azurerm_role_assignment.app_openai_user,
        azurerm_role_assignment.owner_openai_user,
        azurerm_role_assignment.owner_state_bootstrap,
        azurerm_role_assignment.owner_state_app,
        azurerm_role_assignment.owner_state_search,
        azurerm_role_assignment.deploy_contributor,
        azurerm_role_assignment.deploy_identity_operator,
        azurerm_role_assignment.deploy_state_app,
        azurerm_role_assignment.gateway_openai_user_primary,
        azurerm_role_assignment.gateway_openai_user_failover,
        azurerm_role_assignment.owner_state_gateway,
        azurerm_role_assignment.gateway_metrics_publisher,
      ] : startswith(lower(a.scope), lower("/subscriptions/${var.subscription_id}/resourceGroups/"))
    ])
    error_message = "Every role assignment must be scoped inside one of this subscription's resource groups."
  }

  # "On the account" covers the account itself, any scope above it (which it would inherit)
  # and any scope below it. The trailing slashes make the prefix test match whole path
  # segments, so rg-releaselens is not taken for a parent of rg-releaselens-bootstrap.
  assert {
    condition = alltrue([
      for a in [
        azurerm_role_assignment.app_openai_user,
        azurerm_role_assignment.owner_openai_user,
        azurerm_role_assignment.owner_state_bootstrap,
        azurerm_role_assignment.owner_state_app,
        azurerm_role_assignment.owner_state_search,
        azurerm_role_assignment.deploy_contributor,
        azurerm_role_assignment.deploy_identity_operator,
        azurerm_role_assignment.deploy_state_app,
        azurerm_role_assignment.gateway_openai_user_primary,
        azurerm_role_assignment.gateway_openai_user_failover,
        azurerm_role_assignment.owner_state_gateway,
        azurerm_role_assignment.gateway_metrics_publisher,
        ] : !(a.principal_id == azurerm_user_assigned_identity.deploy.principal_id && (
          startswith(lower("${azurerm_cognitive_account.openai.id}/"), lower("${trimsuffix(a.scope, "/")}/")) ||
          startswith(lower(a.scope), lower("${azurerm_cognitive_account.openai.id}/"))
      ))
    ])
    error_message = "The deploy identity must have no role on the Azure OpenAI account, directly or inherited: with one, CI could turn key authentication back on."
  }
}

# The gateway identity calls both accounts' models and nothing else. It is a different principal
# from the app identity and the deploy identity, and each account gets its own assignment (spec
# §3.1). The state container role is the owner's, as for the other stacks' containers.
run "gateway_role_assignments" {
  command = plan

  assert {
    condition     = azurerm_role_assignment.gateway_openai_user_primary.role_definition_name == "Cognitive Services OpenAI User" && azurerm_role_assignment.gateway_openai_user_primary.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.CognitiveServices/accounts/aoai-releaselens-a1b2c3" && azurerm_role_assignment.gateway_openai_user_primary.principal_id == "77777777-7777-7777-7777-777777777777"
    error_message = "gateway_openai_user_primary must give the gateway identity Cognitive Services OpenAI User on the australiaeast account."
  }

  assert {
    condition     = azurerm_role_assignment.gateway_openai_user_failover.role_definition_name == "Cognitive Services OpenAI User" && azurerm_role_assignment.gateway_openai_user_failover.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.CognitiveServices/accounts/aoai-releaselens-sea-a1b2c3" && azurerm_role_assignment.gateway_openai_user_failover.principal_id == "77777777-7777-7777-7777-777777777777"
    error_message = "gateway_openai_user_failover must give the gateway identity Cognitive Services OpenAI User on the Southeast Asia account."
  }

  assert {
    condition     = azurerm_role_assignment.owner_state_gateway.role_definition_name == "Storage Blob Data Contributor" && azurerm_role_assignment.owner_state_gateway.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3/blobServices/default/containers/tfstate-gateway" && azurerm_role_assignment.owner_state_gateway.principal_id == "22222222-2222-2222-2222-222222222222"
    error_message = "owner_state_gateway must give the owner Storage Blob Data Contributor on the tfstate-gateway container's Resource Manager ID."
  }
}

run "handoff_outputs" {
  command = plan

  # SMOKE_OPEN_RUNNER_IP is the ninth variable in the environment. The owner sets it by hand,
  # so it is not in the map.
  assert {
    condition = toset(keys(output.github_environment_variables)) == toset([
      "AZURE_CLIENT_ID",
      "AZURE_TENANT_ID",
      "AZURE_SUBSCRIPTION_ID",
      "TFSTATE_STORAGE_ACCOUNT",
      "APP_IDENTITY_ID",
      "APP_IDENTITY_CLIENT_ID",
      "AZURE_OPENAI_BASE_URL",
      "AZURE_OPENAI_DEPLOYMENT",
    ])
    error_message = "github_environment_variables must have exactly the eight keys AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID, TFSTATE_STORAGE_ACCOUNT, APP_IDENTITY_ID, APP_IDENTITY_CLIENT_ID, AZURE_OPENAI_BASE_URL and AZURE_OPENAI_DEPLOYMENT."
  }

  assert {
    condition     = output.github_environment_variables["AZURE_CLIENT_ID"] == "44444444-4444-4444-4444-444444444444" && output.github_environment_variables["AZURE_CLIENT_ID"] != azurerm_user_assigned_identity.app.client_id
    error_message = "AZURE_CLIENT_ID must be the deploy identity's client ID, not the app identity's: it is the identity the workflows sign in as."
  }

  assert {
    condition = (
      output.github_environment_variables["AZURE_TENANT_ID"] == "11111111-1111-1111-1111-111111111111" &&
      output.github_environment_variables["AZURE_SUBSCRIPTION_ID"] == "00000000-0000-0000-0000-000000000000" &&
      output.github_environment_variables["TFSTATE_STORAGE_ACCOUNT"] == "strlstatea1b2c3" &&
      output.github_environment_variables["APP_IDENTITY_ID"] == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-app" &&
      output.github_environment_variables["APP_IDENTITY_CLIENT_ID"] == "66666666-6666-6666-6666-666666666666" &&
      output.github_environment_variables["AZURE_OPENAI_BASE_URL"] == "https://aoai-releaselens-a1b2c3.openai.azure.com/openai/v1/" &&
      output.github_environment_variables["AZURE_OPENAI_DEPLOYMENT"] == "releaselens-chat"
    )
    error_message = "github_environment_variables must map the tenant, the subscription, the state account's name, the app identity's resource ID and client ID, the v1 base URL and the deployment name to their keys."
  }

  assert {
    condition = (
      output.deploy_identity_client_id == output.github_environment_variables["AZURE_CLIENT_ID"] &&
      output.tenant_id == output.github_environment_variables["AZURE_TENANT_ID"] &&
      output.subscription_id == output.github_environment_variables["AZURE_SUBSCRIPTION_ID"] &&
      output.tfstate_storage_account == output.github_environment_variables["TFSTATE_STORAGE_ACCOUNT"] &&
      output.app_identity_id == output.github_environment_variables["APP_IDENTITY_ID"] &&
      output.app_identity_client_id == output.github_environment_variables["APP_IDENTITY_CLIENT_ID"] &&
      output.azure_openai_base_url == output.github_environment_variables["AZURE_OPENAI_BASE_URL"] &&
      output.azure_openai_deployment == output.github_environment_variables["AZURE_OPENAI_DEPLOYMENT"]
    )
    error_message = "Each named handoff output must equal its entry in github_environment_variables."
  }
}
