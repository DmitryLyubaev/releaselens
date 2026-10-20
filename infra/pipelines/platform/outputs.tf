# The values the ReleaseLens-P8-Delivery variable group is written from.
output "storage_account_name" { value = azurerm_storage_account.platform.name }
output "search_group" { value = azurerm_resource_group.search.name }
output "search_service_name" { value = "p8-search-${random_string.suffix.result}" }
output "tenant_id" { value = var.tenant_id }
output "subscription_id" { value = var.subscription_id }
output "principals" {
  value = { for name, identity in azurerm_user_assigned_identity.delivery : name => { client_id = identity.client_id, object_id = identity.principal_id } }
}
