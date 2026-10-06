# The AI gateway's Entra app (spec §3.1). API Management validates tokens issued for it, so a
# caller needs a token for this app and nothing else. It holds no secret and no certificate:
# nobody signs in as the app, they only get tokens for it.
#
# The azuread provider signs in as the owner through az, and works in the same tenant as the
# azurerm provider.

# The Azure CLI's client ID comes from here and never from a literal. The key is
# MicrosoftAzureCli (the provider's list, in go-azure-sdk's application_ids.go).
data "azuread_application_published_app_ids" "well_known" {}

# The app role's and the scope's IDs are generated, once, and kept in state.
resource "random_uuid" "gateway_invoke_role" {}

resource "random_uuid" "gateway_access_as_user_scope" {}

resource "azuread_application" "gateway" {
  display_name     = "releaselens-ai-gateway"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azurerm_client_config.current.object_id]

  api {
    # Version 2 tokens, whose audience is the client ID and whose issuer is the v2.0 endpoint.
    requested_access_token_version = 2

    oauth2_permission_scope {
      id                         = random_uuid.gateway_access_as_user_scope.result
      value                      = "access_as_user"
      type                       = "User"
      enabled                    = true
      admin_consent_display_name = "Call the AI gateway as the signed-in user"
      admin_consent_description  = "Lets a client call the AI gateway on behalf of the signed-in user."
      user_consent_display_name  = "Call the AI gateway as you"
      user_consent_description   = "Lets a client call the AI gateway on your behalf."
    }
  }

  # The only role. Users and applications (the app and the deploy identity) are assigned it.
  app_role {
    id                   = random_uuid.gateway_invoke_role.result
    value                = "Gateway.Invoke"
    display_name         = "Gateway.Invoke"
    description          = "Call the AI gateway's chat completions."
    allowed_member_types = ["User", "Application"]
    enabled              = true
  }

  # The identifier URI holds the app's own client ID, which exists only after the app does, so
  # it is its own resource below and not set here.
  lifecycle {
    ignore_changes = [identifier_uris]
  }
}

resource "azuread_application_identifier_uri" "gateway" {
  application_id = azuread_application.gateway.id
  identifier_uri = "api://${azuread_application.gateway.client_id}"
}

# Pre-authorising the Azure CLI on the one delegated scope lets `az` get a token for the gateway
# without a consent prompt.
resource "azuread_application_pre_authorized" "azure_cli" {
  application_id       = azuread_application.gateway.id
  authorized_client_id = data.azuread_application_published_app_ids.well_known.result["MicrosoftAzureCli"]
  permission_ids       = [random_uuid.gateway_access_as_user_scope.result]
}

# With an assignment required, only an identity that holds Gateway.Invoke can get a token at all.
resource "azuread_service_principal" "gateway" {
  client_id                    = azuread_application.gateway.client_id
  app_role_assignment_required = true
  owners                       = [data.azurerm_client_config.current.object_id]
}
