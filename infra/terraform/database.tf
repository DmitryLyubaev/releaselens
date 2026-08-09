resource "random_password" "postgres" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}"
}

resource "azurerm_postgresql_flexible_server" "this" {
  name                = "${var.prefix}-pg-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

  version    = "16"
  sku_name   = "B_Standard_B1ms" # Burstable, per the spec. Anything larger is money for nothing here.
  storage_mb = 32768

  administrator_login    = var.postgres_admin_username
  administrator_password = random_password.postgres.result

  backup_retention_days         = 7
  geo_redundant_backup_enabled  = false
  public_network_access_enabled = true

  zone = "1"

  lifecycle {
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "this" {
  name      = "releaselens"
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# pgvector must be allow-listed at the server level before CREATE EXTENSION works.
resource "azurerm_postgresql_flexible_server_configuration" "azure_extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "VECTOR,PG_TRGM"
}

# Container Apps egress IPs are not fixed, so this opens the server to Azure services.
# Acceptable for a demonstration deployment that is destroyed after each session;
# a long-lived deployment would use a private endpoint and VNet integration instead.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.this.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_key_vault_secret" "database_connection" {
  name = "database-connection-string"
  value = format(
    "Host=%s;Port=5432;Database=%s;Username=%s;Password=%s;SSL Mode=Require;Trust Server Certificate=true",
    azurerm_postgresql_flexible_server.this.fqdn,
    azurerm_postgresql_flexible_server_database.this.name,
    var.postgres_admin_username,
    random_password.postgres.result
  )
  key_vault_id = azurerm_key_vault.this.id
  depends_on   = [azurerm_role_assignment.deployer_secrets]
}
