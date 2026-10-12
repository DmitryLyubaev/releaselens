variables {
  subscription_id    = "00000000-0000-0000-0000-000000000000"
  budget_alert_email = "owner@example.com"
  budget_start_date  = "2026-10-01T00:00:00Z"
}
mock_provider "azapi" {}
mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_storage_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.Storage/storageAccounts/mockstate"
    }
  }
  mock_resource "azurerm_storage_container" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.Storage/storageAccounts/mockstate/blobServices/default/containers/tfstate-webapp"
    }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mock"
      principal_id = "00000000-0000-0000-0000-000000000002"
      client_id    = "00000000-0000-0000-0000-000000000003"
    }
  }
}
mock_provider "azuread" {
  override_during = plan
  mock_data "azuread_application_published_app_ids" {
    defaults = { result = { MicrosoftAzureCli = "00000000-0000-0000-0000-000000000009" } }
  }
  mock_resource "azuread_application" {
    defaults = {
      id        = "/applications/00000000-0000-0000-0000-000000000004"
      client_id = "00000000-0000-0000-0000-000000000005"
      object_id = "00000000-0000-0000-0000-000000000004"
    }
  }
  mock_resource "azuread_service_principal" {
    defaults = {
      id        = "/servicePrincipals/00000000-0000-0000-0000-000000000006"
      object_id = "00000000-0000-0000-0000-000000000006"
    }
  }
}
mock_provider "random" {
  override_during = plan
  mock_resource "random_string" { defaults = { result = "mock12" } }
  mock_resource "random_uuid" { defaults = { result = "00000000-0000-0000-0000-000000000007" } }
}
override_data {
  target = data.azurerm_client_config.current
  values = {
    object_id = "00000000-0000-0000-0000-000000000001"
    tenant_id = "00000000-0000-0000-0000-000000000008"
  }
}
override_resource {
  target          = azurerm_user_assigned_identity.webapp
  override_during = plan
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.ManagedIdentity/userAssignedIdentities/webapp"
    principal_id = "00000000-0000-0000-0000-000000000010"
    client_id    = "00000000-0000-0000-0000-000000000011"
  }
}
override_resource {
  target          = random_uuid.webapp_use_role
  override_during = plan
  values          = { result = "00000000-0000-0000-0000-000000000012" }
}
override_resource {
  target          = random_uuid.webapp_smoke_role
  override_during = plan
  values          = { result = "00000000-0000-0000-0000-000000000013" }
}

run "registration_and_federation" {
  command = plan
  assert {
    condition     = azuread_service_principal.webapp.app_role_assignment_required && azuread_application.webapp.sign_in_audience == "AzureADMyOrg"
    error_message = "Front-end access must be single-tenant and require assignment."
  }
  assert {
    condition = length(azuread_application.webapp.app_role) == 2 && alltrue([
      for role in azuread_application.webapp.app_role :
      role.enabled && (role.value == "WebApp.Use" ? role.allowed_member_types == toset(["User"]) && role.id == "00000000-0000-0000-0000-000000000012" : role.value == "WebApp.Smoke" && role.allowed_member_types == toset(["Application"]) && role.id == "00000000-0000-0000-0000-000000000013")
    ])
    error_message = "Use is a user role and Smoke an application role, with distinct IDs."
  }
  assert {
    condition     = azuread_app_role_assignment.webapp_owner.principal_object_id == "00000000-0000-0000-0000-000000000001" && azuread_app_role_assignment.webapp_owner.app_role_id == "00000000-0000-0000-0000-000000000012" && azuread_app_role_assignment.webapp_owner.resource_object_id == azuread_service_principal.webapp.object_id
    error_message = "Only the owner must receive the Use assignment on the front-end app."
  }
  assert {
    condition     = azuread_app_role_assignment.webapp_deploy.principal_object_id == "00000000-0000-0000-0000-000000000002" && azuread_app_role_assignment.webapp_deploy.app_role_id == "00000000-0000-0000-0000-000000000013" && azuread_app_role_assignment.webapp_deploy.resource_object_id == azuread_service_principal.webapp.object_id
    error_message = "The existing deploy identity must receive only Smoke on the front-end app."
  }
  assert {
    condition     = azuread_application.webapp.web[0].redirect_uris == toset(["https://releaselens-web-mock12.azurewebsites.net/.auth/login/aad/callback", "https://releaselens-web-mock12-staging.azurewebsites.net/.auth/login/aad/callback"]) && output.webapp_name == "releaselens-web-mock12"
    error_message = "Both callback hosts must derive from the stable bootstrap suffix."
  }
  assert {
    condition     = azuread_application_federated_identity_credential.webapp.subject == "00000000-0000-0000-0000-000000000010" && azuread_application_federated_identity_credential.webapp.issuer == "https://login.microsoftonline.com/00000000-0000-0000-0000-000000000008/v2.0" && azuread_application_federated_identity_credential.webapp.audiences == tolist(["api://AzureADTokenExchange"]) && azuread_application_federated_identity_credential.webapp.application_id == azuread_application.webapp.id
    error_message = "Easy Auth federation must trust only this managed identity in this tenant."
  }
  assert {
    condition     = azuread_application_pre_authorized.webapp.application_id == azuread_application.gateway.id && azuread_application_pre_authorized.webapp.authorized_client_id == azuread_application.webapp.client_id && azuread_application_pre_authorized.webapp.permission_ids == toset(["00000000-0000-0000-0000-000000000007"])
    error_message = "Pre-authorization must select only the existing delegated gateway scope."
  }
  assert {
    condition     = one(azuread_application.webapp.required_resource_access).resource_app_id == azuread_application.gateway.client_id && one(one(azuread_application.webapp.required_resource_access).resource_access).id == random_uuid.gateway_access_as_user_scope.result && one(one(azuread_application.webapp.required_resource_access).resource_access).type == "Scope"
    error_message = "The front-end registration must request the delegated gateway scope."
  }
  assert {
    condition     = length(azuread_application.webapp.password) == 0
    error_message = "The front end must carry no client secret."
  }
  assert {
    condition     = azurerm_storage_container.webapp.name == "tfstate-webapp" && azurerm_storage_container.webapp.container_access_type == "private" && azurerm_storage_container.webapp.storage_account_id == azurerm_storage_account.state.id
    error_message = "The session stack must have private separate remote state in the existing state account."
  }
  assert {
    condition     = azurerm_role_assignment.owner_state_webapp.scope == azurerm_storage_container.webapp.id && azurerm_role_assignment.owner_state_webapp.principal_id == "00000000-0000-0000-0000-000000000001" && azurerm_role_assignment.owner_state_webapp.role_definition_name == "Storage Blob Data Contributor"
    error_message = "Only the applying owner receives the webapp state data role."
  }
}
