variables {
  subscription_id    = "00000000-0000-0000-0000-000000000000"
  budget_alert_email = "owner@example.com"
  budget_start_date  = "2026-10-01T00:00:00Z"
}

# The gateway's Entra app, its service principal and the three Gateway.Invoke assignments (spec
# §3.1). Every ID below is distinct, so an assignment given the wrong principal, resource or role
# fails its assertion instead of passing on a shared mock value.
mock_provider "azuread" {
  override_during = plan

  # The Azure CLI's client ID comes from here, never from a literal in the stack.
  mock_data "azuread_application_published_app_ids" {
    defaults = {
      result = {
        MicrosoftAzureCli = "99999999-9999-9999-9999-999999999999"
        MicrosoftGraph    = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
      }
    }
  }
}

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
  target          = azurerm_user_assigned_identity.app
  override_during = plan
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-app"
    principal_id = "55555555-5555-5555-5555-555555555555"
    client_id    = "66666666-6666-6666-6666-666666666666"
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
  target          = random_uuid.gateway_access_as_user_scope
  override_during = plan
  values = {
    result = "cccccccc-cccc-cccc-cccc-cccccccccccc"
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

# The owner is whoever applies the stack: the signed-in principal.
override_data {
  target = data.azurerm_client_config.current
  values = {
    object_id = "22222222-2222-2222-2222-222222222222"
    tenant_id = "11111111-1111-1111-1111-111111111111"
  }
}

run "gateway_application" {
  command = plan

  assert {
    condition     = azuread_application.gateway.display_name == "releaselens-ai-gateway"
    error_message = "The gateway's app registration must be named releaselens-ai-gateway."
  }

  assert {
    condition     = azuread_application.gateway.sign_in_audience == "AzureADMyOrg"
    error_message = "The gateway's app registration must be single-tenant."
  }

  assert {
    condition     = length(azuread_application.gateway.api) == 1 && azuread_application.gateway.api[0].requested_access_token_version == 2
    error_message = "The gateway's app must issue version 2 access tokens."
  }

  assert {
    condition     = azuread_application.gateway.owners == toset(["22222222-2222-2222-2222-222222222222"])
    error_message = "The app's only owner must be the signed-in owner."
  }

  assert {
    condition     = azuread_application_identifier_uri.gateway.identifier_uri == "api://eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee" && azuread_application_identifier_uri.gateway.application_id == azuread_application.gateway.id
    error_message = "The identifier URI must be api://<the app's client ID>, set on the gateway's app."
  }
}

run "gateway_app_role" {
  command = plan

  assert {
    condition     = length(azuread_application.gateway.app_role) == 1
    error_message = "The gateway's app must have exactly one app role."
  }

  assert {
    condition = alltrue([
      for r in azuread_application.gateway.app_role :
      r.value == "Gateway.Invoke" &&
      r.display_name == "Gateway.Invoke" &&
      r.enabled == true &&
      r.allowed_member_types == toset(["User", "Application"]) &&
      r.id == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    ])
    error_message = "The app role must be Gateway.Invoke (value and display name), enabled, for User and Application members, with the generated ID."
  }
}

run "gateway_delegated_scope" {
  command = plan

  assert {
    condition     = length(azuread_application.gateway.api[0].oauth2_permission_scope) == 1
    error_message = "The gateway's app must have exactly one delegated scope."
  }

  assert {
    condition = alltrue([
      for s in azuread_application.gateway.api[0].oauth2_permission_scope :
      s.value == "access_as_user" &&
      s.enabled == true &&
      s.id == "cccccccc-cccc-cccc-cccc-cccccccccccc"
    ])
    error_message = "The delegated scope must be access_as_user, enabled, with the generated ID."
  }

  assert {
    condition     = random_uuid.gateway_invoke_role.result != random_uuid.gateway_access_as_user_scope.result
    error_message = "The app role and the delegated scope must have different IDs."
  }

  # The Azure CLI's client ID is the one the published-app-IDs data source gives, and only that
  # scope is pre-authorised for it.
  assert {
    condition     = azuread_application_pre_authorized.azure_cli.authorized_client_id == "99999999-9999-9999-9999-999999999999"
    error_message = "The pre-authorised client must be the Azure CLI, from azuread_application_published_app_ids."
  }

  assert {
    condition     = azuread_application_pre_authorized.azure_cli.permission_ids == toset(["cccccccc-cccc-cccc-cccc-cccccccccccc"]) && azuread_application_pre_authorized.azure_cli.application_id == azuread_application.gateway.id
    error_message = "The Azure CLI must be pre-authorised on the access_as_user scope only, on the gateway's app."
  }
}

run "gateway_service_principal" {
  command = plan

  assert {
    condition     = azuread_service_principal.gateway.client_id == "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    error_message = "The service principal must be the gateway app's."
  }

  assert {
    condition     = azuread_service_principal.gateway.app_role_assignment_required == true
    error_message = "The service principal must require an assignment: only an assigned identity may get a token."
  }

  assert {
    condition     = azuread_service_principal.gateway.owners == toset(["22222222-2222-2222-2222-222222222222"])
    error_message = "The service principal's only owner must be the signed-in owner."
  }
}

run "no_secret_or_certificate" {
  command = plan

  assert {
    condition     = length(azuread_application.gateway.password) == 0
    error_message = "The gateway's app must carry no password."
  }
}

# One role, three principals: the owner (a user), the app identity and the deploy identity (both
# applications). The resource is always the gateway's service principal.
run "gateway_invoke_assignments" {
  command = plan

  assert {
    condition     = azuread_app_role_assignment.gateway_invoke_owner.app_role_id == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" && azuread_app_role_assignment.gateway_invoke_owner.principal_object_id == "22222222-2222-2222-2222-222222222222" && azuread_app_role_assignment.gateway_invoke_owner.resource_object_id == "ffffffff-ffff-ffff-ffff-ffffffffffff"
    error_message = "gateway_invoke_owner must give the signed-in owner Gateway.Invoke on the gateway's service principal."
  }

  assert {
    condition     = azuread_app_role_assignment.gateway_invoke_app.app_role_id == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" && azuread_app_role_assignment.gateway_invoke_app.principal_object_id == "55555555-5555-5555-5555-555555555555" && azuread_app_role_assignment.gateway_invoke_app.resource_object_id == "ffffffff-ffff-ffff-ffff-ffffffffffff"
    error_message = "gateway_invoke_app must give the app identity Gateway.Invoke on the gateway's service principal."
  }

  assert {
    condition     = azuread_app_role_assignment.gateway_invoke_deploy.app_role_id == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" && azuread_app_role_assignment.gateway_invoke_deploy.principal_object_id == "33333333-3333-3333-3333-333333333333" && azuread_app_role_assignment.gateway_invoke_deploy.resource_object_id == "ffffffff-ffff-ffff-ffff-ffffffffffff"
    error_message = "gateway_invoke_deploy must give the deploy identity Gateway.Invoke on the gateway's service principal."
  }
}

run "gateway_app_output" {
  command = plan

  assert {
    condition     = output.gateway_app_client_id == "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    error_message = "gateway_app_client_id must be the gateway app's client ID."
  }
}
