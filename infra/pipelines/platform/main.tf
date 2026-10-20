locals {
  tags = { project = "ReleaseLens", purpose = "project8-demo" }
  # Each writer's own state container; test also owns the per-run service's state.
  states = {
    test-state    = "test"
    prod-state    = "prod"
    service-state = "test"
  }
  # Made once, at group or container scope, so a run never creates a role assignment. Search roles
  # sit on the group: the per-run service inherits them, and Test can read the prod index.
  grants = merge(
    { for name in ["test", "prod"] : "${name}-schema" => { principal = name, role = "Search Service Contributor", scope = azurerm_resource_group.search.id } },
    { for name in ["test", "prod"] : "${name}-documents" => { principal = name, role = "Search Index Data Contributor", scope = azurerm_resource_group.search.id } },
    { "verify-query" = { principal = "verify", role = "Search Index Data Reader", scope = azurerm_resource_group.search.id } },
    # The scheduled status stage reads the service's control plane.
    { "verify-control" = { principal = "verify", role = "Reader", scope = azurerm_resource_group.search.id } },
    { for name in keys(var.federation) : "${name}-inputs" => { principal = name, role = "Storage Blob Data Reader", scope = azurerm_storage_container.private["inputs"].id } },
    { for name in keys(var.federation) : "${name}-evidence" => { principal = name, role = "Storage Blob Data Contributor", scope = azurerm_storage_container.private["evidence"].id } },
    { for state, name in local.states : "${name}-${state}" => { principal = name, role = "Storage Blob Data Contributor", scope = azurerm_storage_container.private[state].id } }
  )
}
resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}
resource "azurerm_resource_group" "platform" {
  name     = "rg-p8-platform"
  location = var.location
  tags     = local.tags
}
# Empty between runs: each run's search service is created here and destroyed by its teardown.
resource "azurerm_resource_group" "search" {
  name     = "rg-p8-search"
  location = var.location
  tags     = local.tags
}
resource "azurerm_user_assigned_identity" "delivery" {
  for_each            = var.federation
  name                = "id-p8-${each.key}"
  location            = var.location
  resource_group_name = azurerm_resource_group.platform.name
  tags                = local.tags
}
resource "azurerm_federated_identity_credential" "delivery" {
  for_each                  = var.federation
  name                      = "fic-p8-${each.key}"
  user_assigned_identity_id = azurerm_user_assigned_identity.delivery[each.key].id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = each.value.issuer
  subject                   = each.value.subject
}
resource "azurerm_storage_account" "platform" {
  name                            = "p8${random_string.suffix.result}"
  resource_group_name             = azurerm_resource_group.platform.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  local_user_enabled              = false
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"
  tags                            = local.tags
}
# The owner uploads the frozen release archive to inputs with this.
resource "azurerm_role_assignment" "owner_storage" {
  scope                = azurerm_storage_account.platform.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.owner_object_id
}
resource "azurerm_storage_container" "private" {
  for_each              = toset(["inputs", "test-state", "prod-state", "service-state", "evidence"])
  name                  = each.key
  storage_account_id    = azurerm_storage_account.platform.id
  container_access_type = "private"
  depends_on            = [azurerm_role_assignment.owner_storage]
}
resource "azurerm_role_assignment" "delivery" {
  for_each             = local.grants
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_user_assigned_identity.delivery[each.value.principal].principal_id
  principal_type       = "ServicePrincipal"
}
