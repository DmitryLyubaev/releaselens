# None of these is a credential. They are identifiers, so no output is marked sensitive. Four
# become GitHub environment secrets only so that public run logs mask them: the tenant and
# subscription IDs (amended 2026-09-30), and the state storage account's name and the Azure
# OpenAI base URL (amended 2026-10-01, because the storage name can lead to the tenant ID and the
# base URL shares its suffix). The rest become variables, and appear unmasked in those logs by
# design (spec §4.12). APP_IDENTITY_ID contains the subscription ID, so it shows with that
# segment masked.

output "app_identity_id" {
  description = "Resource ID of the app identity. The app stack takes it as APP_IDENTITY_ID."
  value       = azurerm_user_assigned_identity.app.id
}

output "app_identity_client_id" {
  description = "Client ID of the app identity. The app stack takes it as APP_IDENTITY_CLIENT_ID and sets it as the container's AZURE_CLIENT_ID."
  value       = azurerm_user_assigned_identity.app.client_id
}

output "azure_openai_base_url" {
  description = "The Azure OpenAI account's v1 base URL. The app stack takes it as AZURE_OPENAI_BASE_URL."
  value       = local.azure_openai_base_url
}

output "azure_openai_deployment" {
  description = "Name of the gpt-4.1-mini deployment. The app stack takes it as AZURE_OPENAI_DEPLOYMENT."
  value       = azurerm_cognitive_deployment.chat.name
}

# The benchmark passes these as --deployment. They are not in github_environment_variables: no
# workflow uses them.
output "embedding_small_deployment" {
  description = "Name of the text-embedding-3-small deployment, used by the retrieval benchmark."
  value       = azurerm_cognitive_deployment.embedding_small.name
}

output "embedding_large_deployment" {
  description = "Name of the text-embedding-3-large deployment, used by the retrieval benchmark."
  value       = azurerm_cognitive_deployment.embedding_large.name
}

output "deploy_identity_client_id" {
  description = "Client ID of the deploy identity, which the workflows sign in as. It is the GitHub environment's AZURE_CLIENT_ID."
  value       = azurerm_user_assigned_identity.deploy.client_id
}

output "tenant_id" {
  description = "Entra ID tenant ID, from the owner's sign-in."
  value       = data.azurerm_client_config.current.tenant_id
}

output "subscription_id" {
  description = "Azure subscription ID."
  value       = var.subscription_id
}

output "tfstate_storage_account" {
  description = "Name of the storage account that holds every stack's state."
  value       = azurerm_storage_account.state.name
}

# SMOKE_OPEN_RUNNER_IP, the environment's fifth variable, is set by hand and is not here.
output "github_environment_variables" {
  description = "Every value of the GitHub environment azure except SMOKE_OPEN_RUNNER_IP, by name, ready to copy into the environment: AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID, TFSTATE_STORAGE_ACCOUNT and AZURE_OPENAI_BASE_URL as secrets, the rest as variables."
  value = tomap({
    AZURE_CLIENT_ID         = azurerm_user_assigned_identity.deploy.client_id
    AZURE_TENANT_ID         = data.azurerm_client_config.current.tenant_id
    AZURE_SUBSCRIPTION_ID   = var.subscription_id
    TFSTATE_STORAGE_ACCOUNT = azurerm_storage_account.state.name
    APP_IDENTITY_ID         = azurerm_user_assigned_identity.app.id
    APP_IDENTITY_CLIENT_ID  = azurerm_user_assigned_identity.app.client_id
    AZURE_OPENAI_BASE_URL   = local.azure_openai_base_url
    AZURE_OPENAI_DEPLOYMENT = azurerm_cognitive_deployment.chat.name
  })
}

# The AI gateway stack reads these through terraform_remote_state (spec §3.1). Identifiers, not
# credentials, like everything above. No key exists on either account.
output "gateway_identity_id" {
  description = "Resource ID of the gateway identity. The gateway stack attaches it to API Management."
  value       = azurerm_user_assigned_identity.gateway.id
}

output "gateway_identity_client_id" {
  description = "Client ID of the gateway identity, which API Management's backend credentials use to get a token for the model accounts."
  value       = azurerm_user_assigned_identity.gateway.client_id
}

output "primary_openai_backend_url" {
  description = "The australiaeast account's v1 URL, the gateway's first backend. No trailing slash: API Management appends the operation's path."
  value       = "https://${azurerm_cognitive_account.openai.custom_subdomain_name}.openai.azure.com/openai/v1"
}

output "failover_openai_backend_url" {
  description = "The Southeast Asia account's v1 URL, the gateway's second backend. No trailing slash."
  value       = "https://${azurerm_cognitive_account.failover.custom_subdomain_name}.openai.azure.com/openai/v1"
}

output "failover_test_deployment" {
  description = "Name of the failover-test deployment, which exists on both accounts and is tiny on the australiaeast one."
  value       = local.failover_test_deployment_name
}

# The gateway's Entra app and monitoring, read by the gateway stack the same way.
output "gateway_app_client_id" {
  description = "Client ID of the gateway's Entra app. The gateway stack's token validation takes it as the audience, and callers ask for a token for api://<this value>."
  value       = azuread_application.gateway.client_id
}

output "app_insights_id" {
  description = "Resource ID of Application Insights. The gateway stack's logger and diagnostic point at it."
  value       = azurerm_application_insights.gateway.id
}

# Sensitive although local authentication is off: the string carries the instrumentation key.
output "app_insights_connection_string" {
  description = "Application Insights' connection string, for the gateway's logger."
  value       = azurerm_application_insights.gateway.connection_string
  sensitive   = true
}

# The functions stack reads these through terraform_remote_state (spec §3.3). Identifiers and
# endpoints, not credentials: the ingestion account has no usable key, and no key, connection
# string or SAS of it is output.
output "ingest_identity_id" {
  description = "Resource ID of the ingest identity. The functions stack attaches it to the ingest app."
  value       = azurerm_user_assigned_identity.ingest.id
}

output "ingest_identity_client_id" {
  description = "Client ID of the ingest identity: the ingest app's AzureWebJobsStorage__clientId and AZURE_CLIENT_ID."
  value       = azurerm_user_assigned_identity.ingest.client_id
}

output "ingest_identity_principal_id" {
  description = "Principal ID of the ingest identity. The search stack gives it Search Index Data Contributor."
  value       = azurerm_user_assigned_identity.ingest.principal_id
}

output "tool_identity_id" {
  description = "Resource ID of the tool identity. The functions stack attaches it to the tool app."
  value       = azurerm_user_assigned_identity.tool.id
}

output "tool_identity_client_id" {
  description = "Client ID of the tool identity: the tool app's AzureWebJobsStorage__clientId and AZURE_CLIENT_ID."
  value       = azurerm_user_assigned_identity.tool.client_id
}

output "tool_identity_principal_id" {
  description = "Principal ID of the tool identity. The search stack gives it Search Index Data Reader."
  value       = azurerm_user_assigned_identity.tool.principal_id
}

output "ingest_storage_account_name" {
  description = "Name of the ingestion storage account, which holds artefacts-in, both queues, both apps' host storage and their deployment containers. It is AzureWebJobsStorage__accountName for both apps."
  value       = azurerm_storage_account.ingest.name
}

output "ingest_storage_blob_endpoint" {
  description = "The ingestion account's blob endpoint, with a trailing slash. The deployment containers' URLs are built from it."
  value       = azurerm_storage_account.ingest.primary_blob_endpoint
}

output "ingest_storage_queue_endpoint" {
  description = "The ingestion account's queue endpoint, with a trailing slash."
  value       = azurerm_storage_account.ingest.primary_queue_endpoint
}

output "search_tool_app_client_id" {
  description = "Client ID of the search tool's Entra app. The tool app's authentication takes it as its client ID; version 2 tokens carry it as the audience."
  value       = azuread_application.search_tool.client_id
}

output "search_tool_app_identifier_uri" {
  description = "The search tool app's identifier URI, api://<client ID>. The gateway asks for a token for it when it calls the tool."
  value       = azuread_application_identifier_uri.search_tool.identifier_uri
}
