output "index" {
  value = { name = azapi_data_plane_resource.index.name, host = var.search_host, target = var.target }
}
