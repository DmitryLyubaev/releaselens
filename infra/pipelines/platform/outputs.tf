# The values the ReleaseLens-P8-Delivery variable group is written from. All are sensitive, so the
# owner's apply never prints an identifier or the random suffix; `terraform output -json` reads them.
output "storage_account_name" {
  value     = azurerm_storage_account.platform.name
  sensitive = true
}
output "search_group" {
  value     = azurerm_resource_group.search.name
  sensitive = true
}
output "search_service_name" {
  value     = "p8-search-${random_string.suffix.result}"
  sensitive = true
}
output "tenant_id" {
  value     = var.tenant_id
  sensitive = true
}
output "subscription_id" {
  value     = var.subscription_id
  sensitive = true
}
output "principals" {
  value     = { for name, identity in azurerm_user_assigned_identity.delivery : name => { client_id = identity.client_id, object_id = identity.principal_id } }
  sensitive = true
}
