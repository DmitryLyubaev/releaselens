# Partial configuration: storage_account_name is passed at migration, with
#   terraform init -migrate-state -backend-config=storage_account_name=<account>
# Until then, a git-ignored backend_override.tf containing terraform { backend "local" {} }
# keeps this stack's state local.
terraform {
  backend "azurerm" {
    container_name   = "tfstate-bootstrap"
    key              = "bootstrap.tfstate"
    use_azuread_auth = true
  }
}
