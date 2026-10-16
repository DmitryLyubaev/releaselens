mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_resource_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/foundation-test" }
  }
  mock_resource "azurerm_storage_account" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/foundation-test/providers/Microsoft.Storage/storageAccounts/teststore" }
  }
  mock_resource "azurerm_search_service" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/foundation-test/providers/Microsoft.Search/searchServices/testsearch" }
  }
}
override_resource {
  override_during = plan
  target          = azurerm_storage_container.private["inputs"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/foundation-test/providers/Microsoft.Storage/storageAccounts/teststore/blobServices/default/containers/inputs" }
}
override_resource {
  override_during = plan
  target          = azurerm_storage_container.private["test-state"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/foundation-test/providers/Microsoft.Storage/storageAccounts/teststore/blobServices/default/containers/test-state" }
}
override_resource {
  override_during = plan
  target          = azurerm_storage_container.private["prod-state"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/foundation-test/providers/Microsoft.Storage/storageAccounts/teststore/blobServices/default/containers/prod-state" }
}
override_resource {
  override_during = plan
  target          = azurerm_storage_container.private["evidence"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/foundation-test/providers/Microsoft.Storage/storageAccounts/teststore/blobServices/default/containers/evidence" }
}
variables {
  subscription_id = "00000000-0000-0000-0000-000000000001"
  tenant_id       = "00000000-0000-0000-0000-000000000004"
  owner_object_id = "00000000-0000-0000-0000-000000000005"
  session_name    = "demo-test"
  principals = {
    test   = "00000000-0000-0000-0000-000000000011"
    prod   = "00000000-0000-0000-0000-000000000012"
    verify = "00000000-0000-0000-0000-000000000013"
  }
}
run "bounded_private_service" {
  command = plan
  assert {
    condition     = azurerm_search_service.delivery.sku == "basic" && azurerm_search_service.delivery.replica_count == 1 && azurerm_search_service.delivery.partition_count == 1 && !azurerm_search_service.delivery.local_authentication_enabled
    error_message = "Only the approved single-unit keyless Basic search service is allowed."
  }
  assert {
    condition     = azurerm_search_service.delivery.authentication_failure_mode == null
    error_message = "Keyless Search cannot combine disabled local authentication with a key/RBAC failure mode."
  }
  assert {
    condition     = !azurerm_storage_account.delivery.shared_access_key_enabled && !azurerm_storage_account.delivery.allow_nested_items_to_be_public && length(azurerm_storage_container.private) == 4
    error_message = "Input, two states and evidence must remain private and keyless."
  }
  assert {
    condition     = local.grants["test-state"].scope != local.grants["prod-state"].scope && local.grants["verify-query"].role == "Search Index Data Reader" && !contains(keys(local.grants), "verify-state") && length(local.grants) == 13
    error_message = "The verify connection must not acquire state/schema/document write permissions."
  }
}
