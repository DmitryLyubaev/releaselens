variables {
  subscription_id         = "00000000-0000-0000-0000-000000000000"
  owner_object_id         = "22222222-2222-2222-2222-222222222222"
  tfstate_storage_account = "stexample"
}

# The bootstrap's two identity principals, as placeholders: the state is never read in a test.
# Each is distinct from the owner's and from the other, so a role wired to the wrong principal
# fails its assertion.
override_data {
  target = data.terraform_remote_state.bootstrap
  values = {
    outputs = {
      ingest_identity_principal_id = "33333333-3333-3333-3333-333333333333"
      tool_identity_principal_id   = "44444444-4444-4444-4444-444444444444"
    }
  }
}

mock_provider "azurerm" {
  override_during = plan

  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-search"
    }
  }

  mock_resource "azurerm_search_service" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-search/providers/Microsoft.Search/searchServices/srch-releaselens-a1b2c3"
    }
  }
}

# The service's name, and so the endpoint, are built from the suffix, so the suffix must be
# known at plan.
mock_provider "random" {
  override_during = plan

  mock_resource "random_string" {
    defaults = {
      result = "a1b2c3"
    }
  }
}

run "search_service" {
  command = plan

  assert {
    condition     = azurerm_resource_group.search.name == "rg-releaselens-search" && azurerm_resource_group.search.location == "australiaeast"
    error_message = "The resource group must be rg-releaselens-search, in australiaeast."
  }

  assert {
    condition     = azurerm_search_service.search.resource_group_name == "rg-releaselens-search" && azurerm_search_service.search.location == "australiaeast"
    error_message = "The search service must be in rg-releaselens-search, in australiaeast."
  }

  assert {
    condition     = azurerm_search_service.search.name == "srch-releaselens-a1b2c3"
    error_message = "The search service must be named srch-releaselens- followed by the suffix."
  }

  assert {
    condition     = azurerm_search_service.search.sku == "basic" && azurerm_search_service.search.replica_count == 1 && azurerm_search_service.search.partition_count == 1
    error_message = "The search service must be basic, with 1 replica and 1 partition."
  }

  assert {
    condition     = azurerm_search_service.search.local_authentication_enabled == false
    error_message = "The search service's key authentication must be off."
  }

  assert {
    condition     = azurerm_search_service.search.semantic_search_sku == "free"
    error_message = "The semantic ranker must be on its free plan, which refuses rather than bills past its allowance."
  }

  assert {
    condition     = output.endpoint == "https://srch-releaselens-a1b2c3.search.windows.net"
    error_message = "endpoint must be https://<service name>.search.windows.net."
  }
}

run "owner_roles" {
  command = plan

  assert {
    condition     = azurerm_role_assignment.owner_service_contributor.role_definition_name == "Search Service Contributor" && azurerm_role_assignment.owner_service_contributor.principal_id == "22222222-2222-2222-2222-222222222222" && azurerm_role_assignment.owner_service_contributor.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-search/providers/Microsoft.Search/searchServices/srch-releaselens-a1b2c3"
    error_message = "owner_service_contributor must give owner_object_id Search Service Contributor on the search service."
  }

  assert {
    condition     = azurerm_role_assignment.owner_index_data_contributor.role_definition_name == "Search Index Data Contributor" && azurerm_role_assignment.owner_index_data_contributor.principal_id == "22222222-2222-2222-2222-222222222222" && azurerm_role_assignment.owner_index_data_contributor.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-search/providers/Microsoft.Search/searchServices/srch-releaselens-a1b2c3"
    error_message = "owner_index_data_contributor must give owner_object_id Search Index Data Contributor on the search service."
  }
}

run "function_identity_roles" {
  command = plan

  assert {
    condition     = azurerm_role_assignment.ingest_index_data_contributor.role_definition_name == "Search Index Data Contributor" && azurerm_role_assignment.ingest_index_data_contributor.principal_id == "33333333-3333-3333-3333-333333333333" && azurerm_role_assignment.ingest_index_data_contributor.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-search/providers/Microsoft.Search/searchServices/srch-releaselens-a1b2c3"
    error_message = "ingest_index_data_contributor must give the ingest identity Search Index Data Contributor on the search service."
  }

  assert {
    condition     = azurerm_role_assignment.tool_index_data_reader.role_definition_name == "Search Index Data Reader" && azurerm_role_assignment.tool_index_data_reader.principal_id == "44444444-4444-4444-4444-444444444444" && azurerm_role_assignment.tool_index_data_reader.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-search/providers/Microsoft.Search/searchServices/srch-releaselens-a1b2c3"
    error_message = "tool_index_data_reader must give the tool identity Search Index Data Reader on the search service."
  }
}

run "owner_object_id_must_be_a_guid" {
  command = plan

  variables {
    owner_object_id = "owner@example.com"
  }

  expect_failures = [var.owner_object_id]
}
