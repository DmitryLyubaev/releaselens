locals {
  suffix = substr(sha256("${var.subscription_id}/${var.session_name}"), 0, 8)
  tags   = { project = "ReleaseLens", purpose = "project8-demo", session = var.session_name }
  writers = {
    test = "test-state"
    prod = "prod-state"
  }
  grants = merge(
    { for name in keys(var.principals) : "${name}-inputs" => { principal = name, role = "Storage Blob Data Reader", scope = azurerm_storage_container.private["inputs"].id } },
    { for name in keys(var.principals) : "${name}-evidence" => { principal = name, role = "Storage Blob Data Contributor", scope = azurerm_storage_container.private["evidence"].id } },
    { for name, state in local.writers : "${name}-state" => { principal = name, role = "Storage Blob Data Contributor", scope = azurerm_storage_container.private[state].id } },
    { for name in keys(local.writers) : "${name}-schema" => { principal = name, role = "Search Service Contributor", scope = azurerm_search_service.delivery.id } },
    { for name in keys(local.writers) : "${name}-documents" => { principal = name, role = "Search Index Data Contributor", scope = azurerm_search_service.delivery.id } },
    { "verify-query" = { principal = "verify", role = "Search Index Data Reader", scope = azurerm_search_service.delivery.id } }
  )
}
resource "azurerm_resource_group" "delivery" {
  name     = "rg-p8-search-${var.session_name}"
  location = var.location
  tags     = local.tags
}
resource "azurerm_search_service" "delivery" {
  name                         = "p8-${var.session_name}-${local.suffix}"
  resource_group_name          = azurerm_resource_group.delivery.name
  location                     = var.location
  sku                          = "basic"
  replica_count                = 1
  partition_count              = 1
  local_authentication_enabled = false
  semantic_search_sku          = "free"
  tags                         = local.tags
}
resource "azurerm_storage_account" "delivery" {
  name                            = "p8${replace(var.session_name, "-", "")}${local.suffix}"
  resource_group_name             = azurerm_resource_group.delivery.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"
  tags                            = local.tags
}
resource "azurerm_role_assignment" "owner_storage" {
  scope                = azurerm_storage_account.delivery.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.owner_object_id
}
resource "azurerm_storage_container" "private" {
  for_each              = toset(["inputs", "test-state", "prod-state", "evidence"])
  name                  = each.key
  storage_account_id    = azurerm_storage_account.delivery.id
  container_access_type = "private"
  depends_on            = [azurerm_role_assignment.owner_storage]
}
resource "azurerm_role_assignment" "delivery" {
  for_each             = local.grants
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = var.principals[each.value.principal]
}
resource "azurerm_role_assignment" "owner_search" {
  for_each             = toset(["Search Service Contributor", "Search Index Data Contributor"])
  scope                = azurerm_search_service.delivery.id
  role_definition_name = each.key
  principal_id         = var.owner_object_id
}
