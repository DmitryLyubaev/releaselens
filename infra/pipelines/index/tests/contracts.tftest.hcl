mock_provider "azapi" {
  override_during = plan
}
variables {
  subscription_id = "00000000-0000-0000-0000-000000000001"
  tenant_id       = "00000000-0000-0000-0000-000000000004"
  search_host     = "owned.search.windows.net"
  target          = "test"
  schema_file     = "tests/schema.json"
}
run "exact_test_schema" {
  command = plan
  assert {
    condition     = azapi_data_plane_resource.index.type == "Microsoft.Search/searchServices/indexes@2024-07-01" && azapi_data_plane_resource.index.parent_id == "owned.search.windows.net" && azapi_data_plane_resource.index.name == "p8-test" && azapi_data_plane_resource.index.body.name == "p8-test" && azapi_data_plane_resource.index.body.fields[1].dimensions == 1536
    error_message = "Target binding and frozen schema dimensions must survive the Terraform adapter."
  }
}
run "separate_prod_schema" {
  command = plan
  variables { target = "demo-prod" }
  assert {
    condition     = azapi_data_plane_resource.index.name == "p8-demo-prod" && azapi_data_plane_resource.index.body.name == "p8-demo-prod"
    error_message = "Prod must have its own plan target; do not reuse test's index name."
  }
}
