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
