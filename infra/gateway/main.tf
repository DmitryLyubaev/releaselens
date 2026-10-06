# A short-lived stack: the owner applies it at the start of a gateway session and destroys it at
# the end (spec §3.2), so nothing here has prevent_destroy. API Management bills by the hour
# whether or not it is called.

# A group of its own, not rg-releaselens: there, the nightly destroy's empty-group check would
# find the service and fail.
resource "azurerm_resource_group" "gateway" {
  name     = "rg-releaselens-gateway"
  location = var.location
}

# The service's name is its gateway's host, so it must be unique across Azure. A new suffix each
# session, because a deleted service's name stays reserved for 48 hours unless purged, and a
# failed purge must not block the next session.
resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_api_management" "gateway" {
  name                = "apim-releaselens-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.gateway.name
  location            = azurerm_resource_group.gateway.location
  sku_name            = "BasicV2_1"
  publisher_name      = "ReleaseLens"
  publisher_email     = var.publisher_email

  # The gateway identity, from the bootstrap, holds its roles there (infra/bootstrap/roles.tf).
  # The gateway calls the models and publishes metrics as it. No system-assigned identity: its
  # roles would have to be granted here, every session, and this stack grants none.
  identity {
    type         = "UserAssigned"
    identity_ids = [local.bootstrap.gateway_identity_id]
  }
}

# The values the policies refer to as {{name}}. None is secret: they are identifiers and limits,
# kept out of the repository by coming from the bootstrap's state and the variables (spec §3.2).
locals {
  named_values = {
    "tenant-id"                  = local.bootstrap.tenant_id
    "gateway-app-client-id"      = local.bootstrap.gateway_app_client_id
    "gateway-identity-client-id" = local.bootstrap.gateway_identity_client_id
    "tokens-per-minute"          = tostring(var.tokens_per_minute)
    "tokens-per-day"             = tostring(var.tokens_per_day)
    # The outbound policy labels the answer primary or secondary by comparing the host the
    # request went to with this one.
    "primary-backend-host" = regex("^https://([^/]+)", local.bootstrap.primary_openai_backend_url)[0]
  }
}

resource "azurerm_api_management_named_value" "this" {
  for_each = local.named_values

  name                = each.key
  display_name        = each.key
  resource_group_name = azurerm_resource_group.gateway.name
  api_management_name = azurerm_api_management.gateway.name
  value               = each.value
  secret              = false
}
