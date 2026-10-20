mock_provider "azurerm" {
  override_during = plan
}
variables {
  subscription_id     = "00000000-0000-0000-0000-000000000001"
  tenant_id           = "00000000-0000-0000-0000-000000000004"
  search_service_name = "p8-search-a1b2c3"
  resource_group_name = "rg-p8-search"
  location            = "australiaeast"
}
run "one_basic_keyless_service" {
  command = plan
  assert {
    condition     = azurerm_search_service.search.sku == "basic" && azurerm_search_service.search.replica_count == 1 && azurerm_search_service.search.partition_count == 1 && !azurerm_search_service.search.local_authentication_enabled && azurerm_search_service.search.semantic_search_sku == "free"
    error_message = "Only the approved single-unit keyless Basic search service, with the free semantic tier."
  }
  assert {
    condition     = azurerm_search_service.search.authentication_failure_mode == null
    error_message = "Keyless Search cannot combine disabled local authentication with a key/RBAC failure mode."
  }
  assert {
    condition     = azurerm_search_service.search.name == "p8-search-a1b2c3" && azurerm_search_service.search.resource_group_name == "rg-p8-search" && azurerm_search_service.search.location == "australiaeast"
    error_message = "The service takes the configured name, in the configured group and region."
  }
  assert {
    condition     = output.search_host == "p8-search-a1b2c3.search.windows.net"
    error_message = "The output is the bare host the settings name."
  }
}
run "approved_name_only" {
  command = plan
  variables {
    search_service_name = "https://p8-search-a1b2c3.search.windows.net"
  }
  expect_failures = [var.search_service_name]
}
