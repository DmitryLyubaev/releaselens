# Both identities live in the bootstrap group and must never move into rg-releaselens. The
# spec gives CI Contributor on that group, which includes writing federated credentials, so
# an identity there would let CI add a trust for itself outside the environment gate
# (spec §4.9).
resource "azurerm_user_assigned_identity" "deploy" {
  name                = "id-releaselens-deploy"
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location
}

resource "azurerm_user_assigned_identity" "app" {
  name                = "id-releaselens-app"
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location
}

# The AI gateway's identity: API Management signs in as it to call both model accounts. It is
# made here, not in the gateway stack, so its role assignments exist and have propagated before
# a gateway session starts, and that stack needs no right to assign roles (spec §3.1). It lives
# in the bootstrap group for the reason above.
resource "azurerm_user_assigned_identity" "gateway" {
  name                = "id-releaselens-gateway"
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location
}

# The only federated credential. The variable's validation holds its subject to this
# repository, to the claim environment:azure exactly (not azure-staging), and to no ref:
# anywhere, so a job without that environment cannot get an Azure token. It does not exist
# until the subject is set, so the budget-only apply can run before the probe.
resource "azurerm_federated_identity_credential" "github_environment" {
  count = var.github_oidc_subject == null ? 0 : 1

  name                      = "github-environment-azure"
  user_assigned_identity_id = azurerm_user_assigned_identity.deploy.id
  issuer                    = "https://token.actions.githubusercontent.com"
  audience                  = ["api://AzureADTokenExchange"]
  subject                   = var.github_oidc_subject
}
