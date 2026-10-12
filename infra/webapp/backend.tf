terraform {
  backend "azurerm" {
    container_name   = "tfstate-webapp"
    key              = "webapp.tfstate"
    use_azuread_auth = true
  }
}
