# Partial configuration: storage_account_name is passed at init, with
#   terraform init -backend-config=storage_account_name=<account>
# The deploy identity has a data role on this container and no key access, so the backend
# authenticates through Entra ID.
terraform {
  backend "azurerm" {
    container_name   = "tfstate-app"
    key              = "app.tfstate"
    use_azuread_auth = true
  }
}
