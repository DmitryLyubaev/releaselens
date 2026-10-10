# A short-lived stack: the owner applies it for one measurement session and destroys it at the
# end (spec §6.4), so nothing here has prevent_destroy.

# A group of its own, not rg-releaselens: there, the nightly destroy's empty-group check would
# find the service and fail (spec §6.2).
resource "azurerm_resource_group" "search" {
  name     = "rg-releaselens-search"
  location = var.location
}

# The service's name is its public endpoint's host, so it must be unique across Azure.
resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_search_service" "search" {
  name                = "srch-releaselens-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.search.name
  location            = azurerm_resource_group.search.location
  sku                 = "basic"
  replica_count       = 1
  partition_count     = 1

  # With key authentication off, every call has to be authorised through Entra ID. The provider
  # stores the service's keys in state, as it does for every search service; they authenticate
  # nothing, because key authentication is off, and none is output or used. azurerm defaults
  # this to true.
  local_authentication_enabled = false

  # The free plan refuses ranked queries past its monthly allowance instead of billing for them
  # (spec §6.3).
  semantic_search_sku = "free"
}

# Role assignments live here, not in bootstrap. The rule that bootstrap holds every assignment
# keeps role-assignment rights away from CI; this stack is applied only by the owner, who holds
# them already (spec §6.2). No other principal gets access.

# To create, update and delete the index.
resource "azurerm_role_assignment" "owner_service_contributor" {
  scope                = azurerm_search_service.search.id
  role_definition_name = "Search Service Contributor"
  principal_id         = var.owner_object_id
}

# To load documents into the index and query it.
resource "azurerm_role_assignment" "owner_index_data_contributor" {
  scope                = azurerm_search_service.search.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = var.owner_object_id
}

# The two Function identities' roles (spec §3.2), on the service only. The identities are
# bootstrap's, read from its state; the roles are here, under the same exception as the owner's,
# because the service they are scoped to belongs to this stack.

# To write the chunks of each artefact the ingest app receives, and to delete its stale ones.
resource "azurerm_role_assignment" "ingest_index_data_contributor" {
  scope                = azurerm_search_service.search.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = local.bootstrap.ingest_identity_principal_id
}

# To query the index, and nothing more: the tool app never writes.
resource "azurerm_role_assignment" "tool_index_data_reader" {
  scope                = azurerm_search_service.search.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = local.bootstrap.tool_identity_principal_id
}
