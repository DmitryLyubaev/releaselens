output "api_url" {
  value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "database_connection_string" {
  description = "Connection string for the Postgres database, including the admin password."
  value       = local.database_connection_string
  sensitive   = true
}

output "pricing_identity" {
  description = "The model, model version and deployment type the app prices each call at."
  value = {
    model           = var.azure_openai_model
    model_version   = var.azure_openai_model_version
    deployment_type = var.azure_openai_deployment_type
  }
}
