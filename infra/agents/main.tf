locals {
  tags = { project = "ReleaseLens", purpose = "project7-demo", session = var.session_name }
  environments = {
    AZP_URL    = var.organization_url
    AZP_POOL   = var.pool_name
    P7_SESSION = var.session_name
    P7Agent    = "ReleaseLens"
  }
}

resource "azurerm_resource_group" "agent" {
  name     = "rg-p7-${var.session_name}"
  location = var.location
  tags     = local.tags
}
resource "azurerm_log_analytics_workspace" "agent" {
  name                = "log-p7-${var.session_name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.agent.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 0.1
  tags                = local.tags
}
resource "azurerm_container_app_environment" "agent" {
  name                       = "cae-p7-${var.session_name}"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.agent.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.agent.id
  logs_destination           = "log-analytics"
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
  tags = local.tags
}
resource "azurerm_key_vault" "agent" {
  name                       = "kv-p7-${var.session_name}"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.agent.name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  purge_protection_enabled   = true
  tags                       = local.tags
}
resource "azurerm_user_assigned_identity" "agent" {
  name                = "id-p7-${var.session_name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.agent.name
  tags                = local.tags
}
resource "azurerm_role_assignment" "reader" {
  scope                = azurerm_key_vault.agent.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.agent.principal_id
}
resource "azurerm_role_assignment" "writer" {
  scope                = azurerm_key_vault.agent.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.owner_object_id
}
resource "terraform_data" "pat_ready" {
  input = { session = var.session_name, source = var.source_sha, vault = azurerm_key_vault.agent.id }
  provisioner "local-exec" {
    command = "python3 ../../scripts/agents/seed_pat.py"
    environment = {
      P7_VAULT_NAME        = azurerm_key_vault.agent.name
      P7_SUBSCRIPTION      = var.subscription_id
      P7_TENANT            = var.tenant_id
      P7_OWNER             = var.owner_account
      P7_PAT_FILE          = var.pat_file
      P7_RECEIPT           = "${var.session_dir}/pat-ready.json"
      P7_SESSION           = var.session_name
      P7_SOURCE            = var.source_sha
      P7_FORBIDDEN_DOMAINS = jsonencode(var.forbidden_account_domains)
    }
    quiet = true
  }
  depends_on = [azurerm_role_assignment.writer]
}

resource "azurerm_container_app_job" "worker" {
  name                         = "p7-${var.session_name}-worker"
  location                     = var.location
  resource_group_name          = azurerm_resource_group.agent.name
  container_app_environment_id = azurerm_container_app_environment.agent.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 900
  replica_retry_limit          = 0
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.agent.id]
  }
  secret {
    name                = "azp-token"
    key_vault_secret_id = "${azurerm_key_vault.agent.vault_uri}secrets/azp-token"
    identity            = azurerm_user_assigned_identity.agent.id
  }
  event_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
    scale {
      min_executions              = 0
      max_executions              = 1
      polling_interval_in_seconds = 30
      rules {
        name             = "pipeline-queue"
        custom_rule_type = "azure-pipelines"
        metadata = {
          organizationURL            = var.organization_url
          poolName                   = var.pool_name
          targetPipelinesQueueLength = "1"
        }
        authentication {
          secret_name       = "azp-token"
          trigger_parameter = "personalAccessToken"
        }
      }
    }
  }
  template {
    container {
      name   = "agent"
      image  = var.image_digest
      cpu    = 2
      memory = "4Gi"
      dynamic "env" {
        for_each = merge(local.environments, { P7_MODE = "worker" })
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name        = "AZP_TOKEN"
        secret_name = "azp-token"
      }
    }
  }
  tags       = local.tags
  depends_on = [terraform_data.pat_ready, azurerm_role_assignment.reader]
}

resource "azurerm_container_app_job" "placeholder" {
  name                         = "p7-${var.session_name}-setup"
  location                     = var.location
  resource_group_name          = azurerm_resource_group.agent.name
  container_app_environment_id = azurerm_container_app_environment.agent.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 900
  replica_retry_limit          = 0
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.agent.id]
  }
  secret {
    name                = "azp-token"
    key_vault_secret_id = "${azurerm_key_vault.agent.vault_uri}secrets/azp-token"
    identity            = azurerm_user_assigned_identity.agent.id
  }
  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }
  template {
    container {
      name   = "agent"
      image  = var.image_digest
      cpu    = 2
      memory = "4Gi"
      dynamic "env" {
        for_each = merge(local.environments, { P7_MODE = "placeholder" })
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name        = "AZP_TOKEN"
        secret_name = "azp-token"
      }
    }
  }
  tags       = local.tags
  depends_on = [terraform_data.pat_ready, azurerm_role_assignment.reader]
}
