output "api_url" {
  value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.this.fqdn
}

output "key_vault_name" {
  value = azurerm_key_vault.this.name
}

output "teardown_command" {
  value       = "terraform destroy -auto-approve"
  description = "Run this at the end of every session. It is the only real cost control."
}
