output "targets" {
  value     = { search_host = "${azurerm_search_service.delivery.name}.search.windows.net", storage_host = "${azurerm_storage_account.delivery.name}.blob.core.windows.net", group_id = azurerm_resource_group.delivery.id, grants = local.grants }
  sensitive = true
}
