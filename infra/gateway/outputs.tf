# Gateway mode is configuration (spec §5): these two are the app's AzureOpenAi:BaseUrl and
# AzureOpenAi:TokenScope, and the harness's and the check workflow's inputs. Identifiers, not
# credentials: no key exists to output.

output "gateway_base_url" {
  description = "The gateway's OpenAI v1 base URL, https://<gateway host>/openai/v1/."
  value       = "${azurerm_api_management.gateway.gateway_url}/${azurerm_api_management_api.v1.path}/${azurerm_api_management_api.v1.version}/"
}

output "gateway_scope" {
  description = "The token scope a caller asks Entra for: api://<gateway app client id>/.default."
  value       = "api://${local.bootstrap.gateway_app_client_id}/.default"
}

# The functions stack reads these through terraform_remote_state (spec §3.3): it publishes the
# search tool as an MCP API on this service, logging through this logger. Identifiers, like the
# two above.
output "api_management_id" {
  description = "Resource ID of the API Management service. The functions stack creates the tool's MCP API, its policy and its named values under it."
  value       = azurerm_api_management.gateway.id
}

output "gateway_url" {
  description = "The service's gateway URL, https://<gateway host>, with no trailing slash. The functions stack builds the tool's MCP URL from it."
  value       = azurerm_api_management.gateway.gateway_url
}

output "app_insights_logger_id" {
  description = "Resource ID of the Application Insights logger. The functions stack's MCP API diagnostic logs through it."
  value       = azurerm_api_management_logger.appi.id
}
