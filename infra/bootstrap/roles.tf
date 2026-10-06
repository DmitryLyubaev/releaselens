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

resource "azurerm_role_assignment" "owner_state_gateway" {
  scope                = azurerm_storage_container.gateway.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# The AI gateway's identity calls the models on both accounts and publishes its metrics to
# Application Insights, and does nothing else here (spec §3.1).
resource "azurerm_role_assignment" "gateway_openai_user_primary" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.gateway.principal_id
}

resource "azurerm_role_assignment" "gateway_openai_user_failover" {
  scope                = azurerm_cognitive_account.failover.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.gateway.principal_id
}

resource "azurerm_role_assignment" "gateway_metrics_publisher" {
  scope                = azurerm_application_insights.gateway.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.gateway.principal_id
}

# Gateway.Invoke, the one app role on the gateway's Entra app, goes to the three identities that
# may call the gateway: the owner, the app and CI. With an assignment required on the service
# principal, nothing else can get a token. These are Entra assignments, not Azure roles, so the
# subscription-scope checks in the tests do not apply to them.
resource "azuread_app_role_assignment" "gateway_invoke_owner" {
  app_role_id         = random_uuid.gateway_invoke_role.result
  principal_object_id = data.azurerm_client_config.current.object_id
  resource_object_id  = azuread_service_principal.gateway.object_id
}

resource "azuread_app_role_assignment" "gateway_invoke_app" {
  app_role_id         = random_uuid.gateway_invoke_role.result
  principal_object_id = azurerm_user_assigned_identity.app.principal_id
  resource_object_id  = azuread_service_principal.gateway.object_id
}

resource "azuread_app_role_assignment" "gateway_invoke_deploy" {
  app_role_id         = random_uuid.gateway_invoke_role.result
  principal_object_id = azurerm_user_assigned_identity.deploy.principal_id
  resource_object_id  = azuread_service_principal.gateway.object_id
}

# CI, as the deploy identity, gets only the three Azure role assignments below: nothing at
# subscription scope, no right to write role assignments, and no role on the Azure OpenAI
# account, so it cannot turn key authentication back on.

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
