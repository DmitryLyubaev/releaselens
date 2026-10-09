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

# Custom metrics with dimensions. Without it Application Insights drops the usage metric's Caller
# dimension, and the gateway's per-caller check (B5) cannot work. azurerm has no argument for it.
# The portal's switch (Usage and estimated costs, Custom metrics) sets this one property, seen
# live on 2026-10-09, so Terraform sets it the same way and the runbook needs no portal step.
resource "azapi_update_resource" "appi_custom_metrics" {
  type        = "Microsoft.Insights/components@2020-02-02"
  resource_id = azurerm_application_insights.gateway.id

  body = {
    properties = {
      CustomMetricsOptedInType = "WithDimensions"
    }
  }
}
