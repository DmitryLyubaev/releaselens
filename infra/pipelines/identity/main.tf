locals {
  tags = { project = "ReleaseLens", purpose = "project8-demo", session = var.session_name }
}
resource "azurerm_resource_group" "identity" {
  name     = "rg-p8-identity-${var.session_name}"
  location = var.location
  tags     = local.tags
}
resource "azurerm_user_assigned_identity" "delivery" {
  for_each            = var.federation
  name                = "id-p8-${var.session_name}-${each.key}"
  location            = var.location
  resource_group_name = azurerm_resource_group.identity.name
  tags                = local.tags
}
resource "azurerm_federated_identity_credential" "delivery" {
  for_each                  = var.federation
  name                      = "fic-p8-${each.key}"
  user_assigned_identity_id = azurerm_user_assigned_identity.delivery[each.key].id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = each.value.issuer
  subject                   = each.value.subject
}
