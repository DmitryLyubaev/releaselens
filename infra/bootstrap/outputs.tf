# None of these is a credential. They are identifiers, so no output is marked sensitive. The
# tenant and subscription IDs become GitHub environment secrets only so that public run logs mask
# them; the rest become variables, and appear unmasked in those logs by design (spec §4.12,
# amended 2026-09-30).

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
  description = "Name of the storage account that holds both stacks' state."
  value       = azurerm_storage_account.state.name
}

# SMOKE_OPEN_RUNNER_IP, the environment's seventh variable, is set by hand and is not here.
output "github_environment_variables" {
  description = "Every value of the GitHub environment azure except SMOKE_OPEN_RUNNER_IP, by name, ready to copy into the environment: AZURE_TENANT_ID and AZURE_SUBSCRIPTION_ID as secrets, the rest as variables."
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
