variables {
  subscription_id    = "00000000-0000-0000-0000-000000000000"
  budget_alert_email = "owner@example.com"
  budget_start_date  = "2026-10-01T00:00:00Z"
}

# The search tool's Entra app, its service principal and the one Tool.Invoke assignment (spec
# §3.1). Every ID below is distinct, and distinct from the gateway app's, so an assignment given
# the wrong principal, resource or role fails its assertion instead of passing on a shared value.
mock_provider "azuread" {
  override_during = plan

  mock_data "azuread_application_published_app_ids" {
    defaults = {
      result = {
        MicrosoftAzureCli = "99999999-9999-9999-9999-999999999999"
      }
    }
  }
}

# The Application Insights custom-metrics switch (monitoring.tf) is an azapi update; mocked here
# like every other provider, so no test reaches Azure.
mock_provider "azapi" {}

mock_provider "azurerm" {
  override_during = plan

  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-deploy"
      principal_id = "33333333-3333-3333-3333-333333333333"
      client_id    = "44444444-4444-4444-4444-444444444444"
    }
  }
}

mock_provider "random" {
  override_during = plan

  mock_resource "random_string" {
    defaults = {
      result = "a1b2c3"
    }
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
  target          = azurerm_user_assigned_identity.tool
  override_during = plan
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-tool"
    principal_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    client_id    = "dddddddd-dddd-dddd-dddd-dddddddddddd"
  }
}

override_resource {
  target          = random_uuid.gateway_invoke_role
  override_during = plan
  values = {
    result = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
  }
}

override_resource {
  target          = random_uuid.tool_invoke_role
  override_during = plan
  values = {
    result = "12121212-1212-1212-1212-121212121212"
  }
}

override_resource {
  target          = azuread_application.gateway
  override_during = plan
  values = {
    id        = "/applications/dddddddd-dddd-dddd-dddd-dddddddddddd"
    client_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    object_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
  }
}

override_resource {
  target          = azuread_service_principal.gateway
  override_during = plan
  values = {
    id        = "/servicePrincipals/ffffffff-ffff-ffff-ffff-ffffffffffff"
    object_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
  }
}

override_resource {
  target          = azuread_application.search_tool
  override_during = plan
  values = {
    id        = "/applications/34343434-3434-3434-3434-343434343434"
    client_id = "56565656-5656-5656-5656-565656565656"
    object_id = "34343434-3434-3434-3434-343434343434"
  }
}

override_resource {
  target          = azuread_service_principal.search_tool
  override_during = plan
  values = {
    id        = "/servicePrincipals/78787878-7878-7878-7878-787878787878"
    object_id = "78787878-7878-7878-7878-787878787878"
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

run "search_tool_application" {
  command = plan

  assert {
    condition     = azuread_application.search_tool.display_name == "releaselens-search-tool"
    error_message = "The search tool's app registration must be named releaselens-search-tool."
  }

  assert {
    condition     = azuread_application.search_tool.sign_in_audience == "AzureADMyOrg"
    error_message = "The search tool's app registration must be single-tenant."
  }

  assert {
    condition     = azuread_application.search_tool.owners == toset(["22222222-2222-2222-2222-222222222222"])
    error_message = "The app's only owner must be the signed-in owner."
  }

  # Version 2 tokens, as the gateway's app issues: their audience is the client ID, and their
  # issuer the v2.0 endpoint.
  assert {
    condition     = length(azuread_application.search_tool.api) == 1 && azuread_application.search_tool.api[0].requested_access_token_version == 2
    error_message = "The search tool's app must issue version 2 access tokens, as the gateway's app does."
  }

  # Nobody signs in to the tool as a user: no delegated scope, so nothing to pre-authorise.
  assert {
    condition     = length(azuread_application.search_tool.api[0].oauth2_permission_scope) == 0
    error_message = "The search tool's app must have no delegated scope."
  }

  assert {
    condition     = azuread_application_identifier_uri.search_tool.identifier_uri == "api://56565656-5656-5656-5656-565656565656" && azuread_application_identifier_uri.search_tool.application_id == "/applications/34343434-3434-3434-3434-343434343434"
    error_message = "The identifier URI must be api://<the search tool app's client ID>, set on the search tool's app."
  }
}

run "search_tool_app_role" {
  command = plan

  assert {
    condition     = length(azuread_application.search_tool.app_role) == 1
    error_message = "The search tool's app must have exactly one app role."
  }

  # Applications only: the gateway's identity is the one caller, and no user is ever assigned.
  assert {
    condition = alltrue([
      for r in azuread_application.search_tool.app_role :
      r.value == "Tool.Invoke" &&
      r.display_name == "Tool.Invoke" &&
      r.enabled == true &&
      r.allowed_member_types == toset(["Application"]) &&
      r.id == "12121212-1212-1212-1212-121212121212"
    ])
    error_message = "The app role must be Tool.Invoke (value and display name), enabled, for Application members only, with the generated ID."
  }

  assert {
    condition     = random_uuid.tool_invoke_role.result != random_uuid.gateway_invoke_role.result
    error_message = "Tool.Invoke and Gateway.Invoke must have different IDs."
  }
}

run "search_tool_service_principal" {
  command = plan

  assert {
    condition     = azuread_service_principal.search_tool.client_id == "56565656-5656-5656-5656-565656565656"
    error_message = "The service principal must be the search tool app's."
  }

  assert {
    condition     = azuread_service_principal.search_tool.app_role_assignment_required == true
    error_message = "The service principal must require an assignment: only an assigned identity may get a token."
  }

  assert {
    condition     = azuread_service_principal.search_tool.owners == toset(["22222222-2222-2222-2222-222222222222"])
    error_message = "The service principal's only owner must be the signed-in owner."
  }
}

run "search_tool_no_secret" {
  command = plan

  assert {
    condition     = length(azuread_application.search_tool.password) == 0
    error_message = "The search tool's app must carry no password."
  }
}

# One role, one principal: the gateway's identity. Not the tool's own identity, not the owner.
run "tool_invoke_assignment" {
  command = plan

  assert {
    condition = (
      azuread_app_role_assignment.tool_invoke_gateway.app_role_id == "12121212-1212-1212-1212-121212121212" &&
      azuread_app_role_assignment.tool_invoke_gateway.principal_object_id == "77777777-7777-7777-7777-777777777777" &&
      azuread_app_role_assignment.tool_invoke_gateway.resource_object_id == "78787878-7878-7878-7878-787878787878"
    )
    error_message = "tool_invoke_gateway must give the gateway identity Tool.Invoke on the search tool's service principal."
  }
}

run "search_tool_outputs" {
  command = plan

  assert {
    condition     = output.search_tool_app_client_id == "56565656-5656-5656-5656-565656565656"
    error_message = "search_tool_app_client_id must be the search tool app's client ID."
  }

  assert {
    condition     = output.search_tool_app_identifier_uri == "api://56565656-5656-5656-5656-565656565656"
    error_message = "search_tool_app_identifier_uri must be the search tool app's identifier URI."
  }
}
