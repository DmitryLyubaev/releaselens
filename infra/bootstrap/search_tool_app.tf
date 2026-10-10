# The search tool's Entra app (spec §3.1). The tool app's authentication accepts tokens issued for
# it, and only from an identity assigned its one role, which is the gateway's identity alone. Like
# the gateway's app, it holds no secret and no certificate: nobody signs in as the app, callers only
# get tokens for it.
#
# Unlike the gateway's app it has no delegated scope and no pre-authorised client: no user ever
# gets a token for the tool, only API Management, signing in as the gateway identity.

# The app role's ID is generated, once, and kept in state.
resource "random_uuid" "tool_invoke_role" {}

resource "azuread_application" "search_tool" {
  display_name     = "releaselens-search-tool"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azurerm_client_config.current.object_id]

  api {
    # Version 2 tokens, as the gateway's app issues: the audience is the client ID, and the issuer
    # the v2.0 endpoint.
    requested_access_token_version = 2
  }

  # The only role, for applications only: the gateway's identity is assigned it in roles.tf.
  app_role {
    id                   = random_uuid.tool_invoke_role.result
    value                = "Tool.Invoke"
    display_name         = "Tool.Invoke"
    description          = "Call the search tool's MCP endpoint."
    allowed_member_types = ["Application"]
    enabled              = true
  }

  # The identifier URI holds the app's own client ID, which exists only after the app does, so it
  # is its own resource below and not set here.
  lifecycle {
    ignore_changes = [identifier_uris]
  }
}

resource "azuread_application_identifier_uri" "search_tool" {
  application_id = azuread_application.search_tool.id
  identifier_uri = "api://${azuread_application.search_tool.client_id}"
}

# With an assignment required, only an identity that holds Tool.Invoke can get a token at all.
resource "azuread_service_principal" "search_tool" {
  client_id                    = azuread_application.search_tool.client_id
  app_role_assignment_required = true
  owners                       = [data.azurerm_client_config.current.object_id]
}
