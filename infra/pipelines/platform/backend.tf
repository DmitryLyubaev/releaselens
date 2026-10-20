# The owner applies this stack from the bootstrap's state account; the account and container are
# given at init.
terraform {
  backend "azurerm" {
    use_azuread_auth = true
    key              = "p8-platform.tfstate"
  }
}
