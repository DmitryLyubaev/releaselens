# One run's search service, in the platform's search group. Replicas and partitions are written out
# because the reviewed plan check reads them.
resource "azurerm_search_service" "search" {
  name                         = var.search_service_name
  resource_group_name          = var.resource_group_name
  location                     = var.location
  sku                          = "basic"
  replica_count                = 1
  partition_count              = 1
  local_authentication_enabled = false
  semantic_search_sku          = "free"
  tags                         = { project = "ReleaseLens", purpose = "project8-demo" }
}
