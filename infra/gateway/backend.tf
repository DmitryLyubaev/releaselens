# Partial configuration: storage_account_name is passed at init, with
#   terraform init -backend-config=storage_account_name=<account>
# Bootstrap creates this container and gives the owner a data role on it. The account has
# shared keys off, so the backend authenticates through Entra ID.
terraform {
  backend "azurerm" {
    container_name   = "tfstate-gateway"
    key              = "gateway.tfstate"
    use_azuread_auth = true
  }
}
