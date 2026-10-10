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

resource "azurerm_role_assignment" "owner_state_functions" {
  scope                = azurerm_storage_container.functions.id
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

# The ingestion and the search tool (spec §3.1). The search service exists only in a session, so
# the two search roles are in infra/search under its exception; everything else is here.
#
# Each app keeps its host storage (AzureWebJobsStorage, identity-based) and its deployment package
# in a host account of its own (ingestion.tf). Its host roles are on that account and nowhere
# else, because the host creates its containers (and the MCP extension its queues) at run time:
# Storage Blob Data Owner, the host's documented minimum, which also covers reading the app's
# deployment container, and the queue and table data roles. Research notes §7 list the set;
# Storage Account Contributor, also listed there, serves only the polling blob trigger, which
# neither app uses. The tool identity has no role on the ingestion account at all.

# The ingest app reads the artefact a message names, works its two queues (the trigger receives
# and deletes; the runtime writes a message to the poison queue after the third failure), and
# embeds chunks.
resource "azurerm_role_assignment" "ingest_artefacts_reader" {
  scope                = azurerm_storage_container.artefacts_in.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.ingest.principal_id
}

# Queues have their Resource Manager ID in id in azurerm 5.x, as containers do.
resource "azurerm_role_assignment" "ingest_queue_events" {
  scope                = azurerm_storage_queue.ingest_events.id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_user_assigned_identity.ingest.principal_id
}

resource "azurerm_role_assignment" "ingest_queue_poison" {
  scope                = azurerm_storage_queue.ingest_events_poison.id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_user_assigned_identity.ingest.principal_id
}

resource "azurerm_role_assignment" "ingest_openai_user" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.ingest.principal_id
}

# Its host storage, on its own host account only.
resource "azurerm_role_assignment" "ingest_host_blob_owner" {
  scope                = azurerm_storage_account.ingest_host.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = azurerm_user_assigned_identity.ingest.principal_id
}

resource "azurerm_role_assignment" "ingest_host_queue_contributor" {
  scope                = azurerm_storage_account.ingest_host.id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_user_assigned_identity.ingest.principal_id
}

resource "azurerm_role_assignment" "ingest_host_table_contributor" {
  scope                = azurerm_storage_account.ingest_host.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_user_assigned_identity.ingest.principal_id
}

# The search tool embeds the query. Its host storage, on its own host account only, includes the
# queues the MCP extension uses.
resource "azurerm_role_assignment" "tool_openai_user" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.tool.principal_id
}

resource "azurerm_role_assignment" "tool_host_blob_owner" {
  scope                = azurerm_storage_account.tool_host.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = azurerm_user_assigned_identity.tool.principal_id
}

resource "azurerm_role_assignment" "tool_host_queue_contributor" {
  scope                = azurerm_storage_account.tool_host.id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_user_assigned_identity.tool.principal_id
}

resource "azurerm_role_assignment" "tool_host_table_contributor" {
  scope                = azurerm_storage_account.tool_host.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_user_assigned_identity.tool.principal_id
}

# Application Insights has local authentication off, so each app sends its telemetry as its own
# identity (APPLICATIONINSIGHTS_AUTHENTICATION_STRING in infra/functions), as the gateway does.
resource "azurerm_role_assignment" "ingest_metrics_publisher" {
  scope                = azurerm_application_insights.gateway.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.ingest.principal_id
}

resource "azurerm_role_assignment" "tool_metrics_publisher" {
  scope                = azurerm_application_insights.gateway.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.tool.principal_id
}

# Event Grid delivers to the queue, and dead-letters, as the system topic's identity. The
# subscription in ingestion.tf depends on both, so they exist before it is created.
resource "azurerm_role_assignment" "eventgrid_queue_sender" {
  scope                = azurerm_storage_queue.ingest_events.id
  role_definition_name = "Storage Queue Data Message Sender"
  principal_id         = azurerm_eventgrid_system_topic.ingest.identity[0].principal_id
}

resource "azurerm_role_assignment" "eventgrid_deadletter_writer" {
  scope                = azurerm_storage_container.deadletter_events.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_eventgrid_system_topic.ingest.identity[0].principal_id
}

# The owner uploads the demo's artefacts and deploys both apps' packages, each to its container in
# that app's host account, with their own sign-in.
resource "azurerm_role_assignment" "owner_artefacts_in" {
  scope                = azurerm_storage_container.artefacts_in.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "owner_deploy_ingest" {
  scope                = azurerm_storage_container.deploy_ingest.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "owner_deploy_tool" {
  scope                = azurerm_storage_container.deploy_tool.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Tool.Invoke, the one app role on the search tool's Entra app, goes to the gateway's identity
# only: API Management signs in as it to call the tool. With an assignment required on the
# service principal, nothing else can get a token for the tool.
resource "azuread_app_role_assignment" "tool_invoke_gateway" {
  app_role_id         = random_uuid.tool_invoke_role.result
  principal_object_id = azurerm_user_assigned_identity.gateway.principal_id
  resource_object_id  = azuread_service_principal.search_tool.object_id
}
