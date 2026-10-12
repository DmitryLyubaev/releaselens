# Persistent identity and registration shared by the main site and staging slot.
# The managed identity authenticates Easy Auth's client assertion only. It has
# no model or gateway application permission; downstream calls use the owner.
locals {
  webapp_name = "releaselens-web-${random_string.suffix.result}"
}

resource "azurerm_user_assigned_identity" "webapp" {
  name                = "id-releaselens-web"
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location
  tags                = local.tags
}
resource "random_uuid" "webapp_use_role" {}
resource "random_uuid" "webapp_smoke_role" {}

resource "azuread_application" "webapp" {
  display_name     = "releaselens-web"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azurerm_client_config.current.object_id]
  api {
    requested_access_token_version = 2
  }
  web {
    # Easy Auth's server-directed sign-in requests code+id_token.
    implicit_grant {
      id_token_issuance_enabled     = true
      access_token_issuance_enabled = false
    }
    redirect_uris = [
      "https://${local.webapp_name}.azurewebsites.net/.auth/login/aad/callback",
      "https://${local.webapp_name}-staging.azurewebsites.net/.auth/login/aad/callback",
    ]
  }
  app_role {
    id                   = random_uuid.webapp_use_role.result
    value                = "WebApp.Use"
    display_name         = "WebApp.Use"
    description          = "Ask questions as the signed-in owner."
    allowed_member_types = ["User"]
    enabled              = true
  }
  app_role {
    id                   = random_uuid.webapp_smoke_role.result
    value                = "WebApp.Smoke"
    display_name         = "WebApp.Smoke"
    description          = "Read safe deployment health only."
    allowed_member_types = ["Application"]
    enabled              = true
  }
  required_resource_access {
    resource_app_id = azuread_application.gateway.client_id
    resource_access {
      id   = random_uuid.gateway_access_as_user_scope.result
      type = "Scope"
    }
  }
  lifecycle {
    ignore_changes = [identifier_uris]
  }
}
resource "azuread_application_identifier_uri" "webapp" {
  application_id = azuread_application.webapp.id
  identifier_uri = "api://${azuread_application.webapp.client_id}"
}
resource "azuread_service_principal" "webapp" {
  client_id                    = azuread_application.webapp.client_id
  app_role_assignment_required = true
  owners                       = [data.azurerm_client_config.current.object_id]
}
resource "azuread_app_role_assignment" "webapp_owner" {
  app_role_id         = random_uuid.webapp_use_role.result
  principal_object_id = data.azurerm_client_config.current.object_id
  resource_object_id  = azuread_service_principal.webapp.object_id
}
resource "azuread_app_role_assignment" "webapp_deploy" {
  app_role_id         = random_uuid.webapp_smoke_role.result
  principal_object_id = azurerm_user_assigned_identity.deploy.principal_id
  resource_object_id  = azuread_service_principal.webapp.object_id
}
resource "azuread_application_federated_identity_credential" "webapp" {
  application_id = azuread_application.webapp.id
  display_name   = "easy-auth-managed-identity"
  description    = "Keyless Easy Auth assertion from the dedicated front-end identity."
  issuer         = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/v2.0"
  audiences      = ["api://AzureADTokenExchange"]
  subject        = azurerm_user_assigned_identity.webapp.principal_id
}
resource "azuread_application_pre_authorized" "webapp" {
  application_id       = azuread_application.gateway.id
  authorized_client_id = azuread_application.webapp.client_id
  permission_ids       = [random_uuid.gateway_access_as_user_scope.result]
}
