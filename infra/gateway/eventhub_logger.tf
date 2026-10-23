# Project 10: the Event Hubs logger and the two named values the log statement uses
# (policies/log-to-eventhub.xml). All of it exists only when event_hub_logging is on; api.tf puts
# the statement into both policies under the same switch.
#
# The hub is the Fabric repo's (infra/event-hubs), with local authentication off, so the logger
# sends as the gateway identity, which that stack gives Azure Event Hubs Data Sender on the hub.
# This stack grants no role.

# azurerm, not azapi: the pinned provider's eventhub block takes a user-assigned identity's client
# ID with the namespace's endpoint, and no connection string (checked in the 5.7.0 schema).
resource "azurerm_api_management_logger" "eventhub" {
  count = var.event_hub_logging ? 1 : 0

  name                = "p10-eventhub"
  resource_group_name = azurerm_resource_group.gateway.name
  api_management_name = azurerm_api_management.gateway.name

  eventhub {
    name                             = var.event_hub_name
    endpoint_uri                     = "${var.event_hub_namespace}.servicebus.windows.net"
    user_assigned_identity_client_id = local.bootstrap.gateway_identity_client_id
  }
}

# The policy reads this as JObject.Parse("{{caller-labels}}"): the named value is put into the
# expression's text, inside a C# string literal. So it is stored compacted, with each backslash
# and double quote escaped for that literal. Secret, because it holds object IDs.
resource "azurerm_api_management_named_value" "caller_labels" {
  count = var.event_hub_logging ? 1 : 0

  name                = "caller-labels"
  display_name        = "caller-labels"
  resource_group_name = azurerm_resource_group.gateway.name
  api_management_name = azurerm_api_management.gateway.name
  value               = replace(replace(jsonencode(jsondecode(var.caller_labels_json)), "\\", "\\\\"), "\"", "\\\"")
  secret              = true
}

resource "azurerm_api_management_named_value" "p10_session" {
  count = var.event_hub_logging ? 1 : 0

  name                = "p10-session"
  display_name        = "p10-session"
  resource_group_name = azurerm_resource_group.gateway.name
  api_management_name = azurerm_api_management.gateway.name
  value               = var.p10_session
  secret              = false
}
