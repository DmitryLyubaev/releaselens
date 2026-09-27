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

# The only federated credential. Its subject names the GitHub environment azure, never a
# branch, so a job without that environment cannot get an Azure token. It does not exist
# until the subject is set, so the budget-only apply can run before the probe.
resource "azurerm_federated_identity_credential" "github_environment" {
  count = var.github_oidc_subject == null ? 0 : 1

  name                      = "github-environment-azure"
  user_assigned_identity_id = azurerm_user_assigned_identity.deploy.id
  issuer                    = "https://token.actions.githubusercontent.com"
  audience                  = ["api://AzureADTokenExchange"]
  subject                   = var.github_oidc_subject
}
