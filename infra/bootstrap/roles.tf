# Every role assignment in the design lives here (spec §4.10). The app stack writes none. The one
# exception is the search stack, which only the owner applies, and which gives the owner two
# roles on its own search service (spec §6.2 of the AI Search benchmark).

# The owner is whoever applies this stack, so the owner's principal is the signed-in one.
# Applied by anyone else, these assignments would move to that principal.
data "azurerm_client_config" "current" {}

resource "azurerm_role_assignment" "app_openai_user" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# For local runs through az login.
resource "azurerm_role_assignment" "owner_openai_user" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = data.azurerm_client_config.current.object_id
}

# The Owner role has no data actions, so without these the owner could not migrate state or
# run the app or search stack locally.
#
# In azurerm 5.x a container created with storage_account_id has its Resource Manager ID in
# id (.../blobServices/default/containers/<name>); 5.0 removed resource_manager_id. url is
# the data-plane URL, which is not a valid role scope.
resource "azurerm_role_assignment" "owner_state_bootstrap" {
  scope                = azurerm_storage_container.bootstrap.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "owner_state_app" {
  scope                = azurerm_storage_container.app.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "owner_state_search" {
  scope                = azurerm_storage_container.search.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# CI, as the deploy identity, gets only the three assignments below: nothing at subscription
# scope, no right to write role assignments, and no role on the Azure OpenAI account, so it
# cannot turn key authentication back on.

resource "azurerm_role_assignment" "deploy_contributor" {
  scope                = azurerm_resource_group.app.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
}

# Lets CI attach the app identity, which lives in the bootstrap group, to the Container App.
resource "azurerm_role_assignment" "deploy_identity_operator" {
  scope                = azurerm_user_assigned_identity.app.id
  role_definition_name = "Managed Identity Operator"
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
}

# The app stack's state and its lock. CI cannot read the bootstrap state.
resource "azurerm_role_assignment" "deploy_state_app" {
  scope                = azurerm_storage_container.app.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
}
