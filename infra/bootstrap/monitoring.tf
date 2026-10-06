# The AI gateway's monitoring (spec §3.1). It lives here, not in the gateway stack, so that usage
# from every gateway session stays in one place after each session's stack is destroyed. Both
# resources cost nothing idle, and the daily cap bounds the workspace's ingestion.
#
# Local authentication is off on both: nothing signs in with an instrumentation key or a shared
# key. The gateway identity publishes metrics with Entra ID, through the role in roles.tf.

resource "azurerm_log_analytics_workspace" "gateway" {
  name                = "log-releaselens"
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 0.1

  local_authentication_enabled = false
  tags                         = local.tags
}

# Workspace-based: its data is stored in the workspace above.
resource "azurerm_application_insights" "gateway" {
  name                = "appi-releaselens"
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location
  workspace_id        = azurerm_log_analytics_workspace.gateway.id
  application_type    = "other"

  local_authentication_enabled = false
  tags                         = local.tags
}
