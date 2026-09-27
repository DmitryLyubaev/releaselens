locals {
  tags = {
    project   = "releaselens"
    managedby = "terraform"
    stack     = "bootstrap"
  }
}

# Suffix for names that must be globally unique.
resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "azurerm_resource_group" "bootstrap" {
  name     = "rg-releaselens-bootstrap"
  location = var.location
  tags     = local.tags
}

# Created here and left empty for the app stack to deploy into, so the CI identity needs
# no right at subscription scope.
resource "azurerm_resource_group" "app" {
  name     = "rg-releaselens"
  location = var.location

  tags = merge(local.tags, {
    contents = "app stack, deployed and destroyed by CI"
  })
}

# The budget is subscription-scoped, so this lock does not protect it. Its action group
# is in this group, and is protected.
resource "azurerm_management_lock" "bootstrap" {
  name       = "lock-releaselens-bootstrap"
  scope      = azurerm_resource_group.bootstrap.id
  lock_level = "CanNotDelete"
  notes      = "Long-lived bootstrap stack. This lock must be lifted to delete anything inside this resource group."
}
