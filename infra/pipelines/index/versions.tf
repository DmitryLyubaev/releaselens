terraform {
  required_version = "= 1.15.8"
  required_providers {
    azapi = { source = "Azure/azapi", version = "= 2.8.0" }
  }
}
provider "azapi" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
}
