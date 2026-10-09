# The gateway logs to the bootstrap's Application Insights, which outlives every session's
# stack (spec §3.1).

# Ingestion authenticates as the gateway identity, which holds Monitoring Metrics Publisher on
# Application Insights (infra/bootstrap/roles.tf): local authentication is off there, so the
# connection string's instrumentation key alone would be refused. The provider requires the
# connection string with identity_client_id. It comes from the bootstrap's state, marked
# sensitive, and is neither output nor written to any file.
resource "azurerm_api_management_logger" "appi" {
  name                = "appi-releaselens"
  resource_group_name = azurerm_resource_group.gateway.name
  api_management_name = azurerm_api_management.gateway.name
  resource_id         = local.bootstrap.app_insights_id

  application_insights {
    connection_string  = local.bootstrap.app_insights_connection_string
    identity_client_id = local.bootstrap.gateway_identity_client_id
  }
}

# Every request is recorded, with no body: no prompt or answer leaves the gateway in a log
# (spec §8). Only the token counts reach the metric.
resource "azurerm_api_management_api_diagnostic" "appi" {
  identifier               = "applicationinsights"
  resource_group_name      = azurerm_resource_group.gateway.name
  api_management_name      = azurerm_api_management.gateway.name
  api_name                 = azurerm_api_management_api.v1.name
  api_management_logger_id = azurerm_api_management_logger.appi.id

  sampling_percentage       = 100
  always_log_errors         = true
  log_client_ip             = false
  verbosity                 = "information"
  http_correlation_protocol = "W3C"

  frontend_request {
    body_bytes = 0
  }

  frontend_response {
    body_bytes = 0
  }

  backend_request {
    body_bytes = 0
  }

  backend_response {
    body_bytes = 0
  }
}

# llm-emit-token-metric publishes only while the diagnostic's metrics switch is on. azurerm has
# no argument for it (PR #28499 is open), so it is set here, on the diagnostic above.
# An in-place update of the azurerm diagnostic would PUT it without `metrics`, switching them off.
resource "azapi_update_resource" "diagnostic_metrics" {
  type        = "Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01"
  resource_id = azurerm_api_management_api_diagnostic.appi.id

  body = {
    properties = {
      metrics = true
    }
  }
}

# Revision 2 is copied from revision 1 with the diagnostic, so its calls are logged, but the copy
# does not carry metrics = true: on 2026-10-09 its calls emitted no token metric. This sets the
# switch on revision 2's own diagnostic, by revision 2's own ID, so azurerm's revision handling
# (which addresses the current revision) never comes into it. The release waits for it.
resource "azapi_update_resource" "diagnostic_metrics_rev2" {
  type        = "Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01"
  resource_id = "${azurerm_api_management_api.v1_rev2.id}/diagnostics/applicationinsights"

  body = {
    properties = {
      metrics = true
    }
  }
}
