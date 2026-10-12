data "terraform_remote_state" "bootstrap" {
  backend = "azurerm"
  config = {
    storage_account_name = var.tfstate_storage_account
    container_name       = "tfstate-bootstrap"
    key                  = "bootstrap.tfstate"
    use_azuread_auth     = true
  }
}
data "terraform_remote_state" "gateway" {
  backend = "azurerm"
  config = {
    storage_account_name = var.tfstate_storage_account
    container_name       = "tfstate-gateway"
    key                  = "gateway.tfstate"
    use_azuread_auth     = true
  }
}
locals {
  bootstrap = data.terraform_remote_state.bootstrap.outputs
  gateway   = data.terraform_remote_state.gateway.outputs
}
