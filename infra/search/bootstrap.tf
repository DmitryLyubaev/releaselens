# The two Function identities' principal IDs (spec §3.2), read from the bootstrap's state, which
# the owner has a data role on, so the identifiers never pass through a file or a terminal.
data "terraform_remote_state" "bootstrap" {
  backend = "azurerm"

  config = {
    storage_account_name = var.tfstate_storage_account
    container_name       = "tfstate-bootstrap"
    key                  = "bootstrap.tfstate"
    use_azuread_auth     = true
  }
}

locals {
  bootstrap = data.terraform_remote_state.bootstrap.outputs
}
