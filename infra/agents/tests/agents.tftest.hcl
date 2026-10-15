mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_resource_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/p7-demo" }
  }
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/p7-demo/providers/Microsoft.OperationalInsights/workspaces/p7-demo" }
  }
  mock_resource "azurerm_key_vault" {
    defaults = {
      id        = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/p7-demo/providers/Microsoft.KeyVault/vaults/p7-demo"
      vault_uri = "https://example.vault.azure.net/"
    }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/p7-demo/providers/Microsoft.ManagedIdentity/userAssignedIdentities/p7-demo"
      principal_id = "00000000-0000-0000-0000-000000000001"
    }
  }
}

variables {
  subscription_id           = "00000000-0000-0000-0000-000000000000"
  tenant_id                 = "00000000-0000-0000-0000-000000000002"
  owner_object_id           = "00000000-0000-0000-0000-000000000003"
  owner_account             = "owner@example.com"
  forbidden_account_domains = ["example-employer.invalid"]
  session_name              = "demo-test"
  organization_url          = "https://dev.azure.com/example"
  pool_name                 = "p7-demo"
  image_digest              = "ghcr.io/example/releaselens-agent@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  pat_file                  = "/tmp/private/pat.txt"
  session_dir               = "/tmp/private"
  source_sha                = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

run "bounded_host" {
  command = plan
  assert {
    condition     = azurerm_container_app_job.worker.event_trigger_config[0].scale[0].rules[0].metadata.organizationURL == "https://dev.azure.com/example" && one([for env in azurerm_container_app_job.worker.template[0].container[0].env : env.value if env.name == "AZP_URL"]) == "https://dev.azure.com/example" && one([for env in azurerm_container_app_job.placeholder.template[0].container[0].env : env.value if env.name == "AZP_URL"]) == "https://dev.azure.com/example"
    error_message = "Canonical organisation URLs must remain unchanged in the scaler and both agents."
  }
  assert {
    condition     = azurerm_container_app_environment.agent.logs_destination == "log-analytics" && azurerm_container_app_environment.agent.log_analytics_workspace_id == azurerm_log_analytics_workspace.agent.id
    error_message = "The dedicated workspace requires an explicit Log Analytics destination before apply."
  }
  assert {
    condition     = azurerm_container_app_job.worker.replica_timeout_in_seconds == 900 && azurerm_container_app_job.worker.replica_retry_limit == 0 && azurerm_container_app_job.worker.template[0].container[0].cpu == 2 && azurerm_container_app_job.worker.template[0].container[0].memory == "4Gi"
    error_message = "Worker limits differ from the approved demo."
  }
  assert {
    condition     = azurerm_container_app_job.worker.event_trigger_config[0].scale[0].min_executions == 0 && azurerm_container_app_job.worker.event_trigger_config[0].scale[0].max_executions == 1 && azurerm_container_app_job.worker.event_trigger_config[0].scale[0].polling_interval_in_seconds == 30
    error_message = "Scale must be min0/max1/poll30 without asserting a global mutex."
  }
  assert {
    condition     = azurerm_container_app_job.placeholder.manual_trigger_config[0].parallelism == 1 && azurerm_container_app_job.worker.event_trigger_config[0].parallelism == 1 && azurerm_container_app_job.worker.event_trigger_config[0].replica_completion_count == 1
    error_message = "Setup must be manual and worker event-driven with one replica."
  }
  assert {
    condition     = azurerm_role_assignment.reader.scope == azurerm_key_vault.agent.id && azurerm_role_assignment.writer.scope == azurerm_key_vault.agent.id && azurerm_role_assignment.reader.role_definition_name == "Key Vault Secrets User" && azurerm_role_assignment.writer.role_definition_name == "Key Vault Secrets Officer"
    error_message = "Roles must be limited to the new dedicated vault."
  }
  assert {
    condition     = azurerm_log_analytics_workspace.agent.daily_quota_gb == 0.1 && azurerm_log_analytics_workspace.agent.retention_in_days == 30 && azurerm_key_vault.agent.purge_protection_enabled
    error_message = "Log bounds and vault soft-delete protection are mandatory."
  }
}

run "organization_url_with_trailing_slash" {
  command = plan
  variables {
    organization_url = "https://dev.azure.com/example-org_123/"
  }
  assert {
    condition     = azurerm_container_app_job.worker.event_trigger_config[0].scale[0].rules[0].metadata.organizationURL == "https://dev.azure.com/example-org_123"
    error_message = "KEDA must receive the organisation URL without a trailing slash."
  }
  assert {
    condition     = one([for env in azurerm_container_app_job.worker.template[0].container[0].env : env.value if env.name == "AZP_URL"]) == "https://dev.azure.com/example-org_123"
    error_message = "The worker must use the same canonical organisation URL as KEDA."
  }
  assert {
    condition     = one([for env in azurerm_container_app_job.placeholder.template[0].container[0].env : env.value if env.name == "AZP_URL"]) == "https://dev.azure.com/example-org_123"
    error_message = "The placeholder must use the same canonical organisation URL as KEDA."
  }
}
