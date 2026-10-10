# Three states, each read from the state account the owner has a data role on, so no identifier
# passes through a file or a terminal (spec §3.3):
# - the bootstrap: the two identities, the three storage accounts, the search tool's registration,
#   the tenant, the gateway's identity and app, the Azure OpenAI URL and Application Insights
# - the search stack: the endpoint
# - the gateway: the API Management service, its gateway URL and its Application Insights logger
# So the bootstrap, then the gateway and the search stack, are applied before this one.

data "terraform_remote_state" "bootstrap" {
  backend = "azurerm"

  config = {
    storage_account_name = var.tfstate_storage_account
    container_name       = "tfstate-bootstrap"
    key                  = "bootstrap.tfstate"
    use_azuread_auth     = true
  }
}

data "terraform_remote_state" "search" {
  backend = "azurerm"

  config = {
    storage_account_name = var.tfstate_storage_account
    container_name       = "tfstate-search"
    key                  = "search.tfstate"
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
  search    = data.terraform_remote_state.search.outputs
  gateway   = data.terraform_remote_state.gateway.outputs
}
