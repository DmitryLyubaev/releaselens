output "identities" {
  value     = { for name, identity in azurerm_user_assigned_identity.delivery : name => { client_id = identity.client_id, object_id = identity.principal_id, id = identity.id } }
  sensitive = true
}
