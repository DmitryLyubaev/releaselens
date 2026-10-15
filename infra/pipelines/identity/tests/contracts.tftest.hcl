mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_resource_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/identity-test" }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/identity-test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/test"
      principal_id = "00000000-0000-0000-0000-000000000002"
      client_id    = "00000000-0000-0000-0000-000000000003"
    }
  }
}
variables {
  subscription_id = "00000000-0000-0000-0000-000000000001"
  tenant_id       = "00000000-0000-0000-0000-000000000004"
  session_name    = "demo-test"
  federation = {
    test   = { issuer = "https://issuer.example/current", subject = "fresh-test" }
    prod   = { issuer = "https://issuer.example/current", subject = "fresh-prod" }
    verify = { issuer = "https://issuer.example/current", subject = "fresh-verify" }
  }
}
run "fresh_scoped_federation" {
  command = plan
  assert {
    condition     = length(azurerm_user_assigned_identity.delivery) == 3 && length(azurerm_federated_identity_credential.delivery) == 3
    error_message = "Exactly three fresh workload identities and their credentials are required."
  }
  assert {
    condition     = azurerm_federated_identity_credential.delivery["prod"].subject == "fresh-prod" && azurerm_federated_identity_credential.delivery["prod"].issuer == "https://issuer.example/current" && length(azurerm_federated_identity_credential.delivery["prod"].audience) == 1 && azurerm_federated_identity_credential.delivery["prod"].audience[0] == "api://AzureADTokenExchange"
    error_message = "Do not substitute a prior federation subject/issuer."
  }
}
