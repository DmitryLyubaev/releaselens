resource "azapi_data_plane_resource" "index" {
  type      = "Microsoft.Search/searchServices/indexes@2024-07-01"
  name      = "p8-${var.target}"
  parent_id = var.search_host
  body      = merge(jsondecode(file(var.schema_file)), { name = "p8-${var.target}" })
}
