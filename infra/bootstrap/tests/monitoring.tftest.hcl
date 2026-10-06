variables {
  subscription_id    = "00000000-0000-0000-0000-000000000000"
  budget_alert_email = "owner@example.com"
  budget_start_date  = "2026-10-01T00:00:00Z"
}

# Log Analytics and workspace-based Application Insights, with local authentication off on both,
# and the gateway identity's one role on Application Insights (spec §3.1). The IDs are distinct,
# so a role given the wrong scope or principal fails its assertion.
mock_provider "azuread" {
  mock_data "azuread_application_published_app_ids" {
    defaults = {
      result = {
        MicrosoftAzureCli = "99999999-9999-9999-9999-999999999999"
      }
    }
  }
}

mock_provider "azurerm" {
  override_during = plan

  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap"
    }
  }

  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-deploy"
      principal_id = "33333333-3333-3333-3333-333333333333"
      client_id    = "44444444-4444-4444-4444-444444444444"
    }
  }

  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.OperationalInsights/workspaces/log-releaselens"
    }
  }

  mock_resource "azurerm_application_insights" {
    defaults = {
      id                = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Insights/components/appi-releaselens"
      connection_string = "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://example.com/"
    }
  }
}

mock_provider "random" {
  override_during = plan

  mock_resource "random_string" {
    defaults = {
      result = "a1b2c3"
    }
  }
}

override_resource {
  target          = azurerm_user_assigned_identity.gateway
  override_during = plan
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-gateway"
    principal_id = "77777777-7777-7777-7777-777777777777"
    client_id    = "88888888-8888-8888-8888-888888888888"
  }
}

# The gateway app's owner is the signed-in principal, and the provider checks that it is a UUID.
override_data {
  target = data.azurerm_client_config.current
  values = {
    object_id = "22222222-2222-2222-2222-222222222222"
    tenant_id = "11111111-1111-1111-1111-111111111111"
  }
}

run "log_analytics" {
  command = plan

  assert {
    condition     = azurerm_log_analytics_workspace.gateway.name == "log-releaselens"
    error_message = "The workspace must be named log-releaselens."
  }

  assert {
    condition     = azurerm_log_analytics_workspace.gateway.resource_group_name == "rg-releaselens-bootstrap" && azurerm_log_analytics_workspace.gateway.location == "australiaeast"
    error_message = "The workspace must be in the bootstrap resource group, in the stack's location."
  }

  assert {
    condition     = azurerm_log_analytics_workspace.gateway.retention_in_days == 30
    error_message = "The workspace must keep data for 30 days."
  }

  assert {
    condition     = azurerm_log_analytics_workspace.gateway.daily_quota_gb == 0.1
    error_message = "The workspace's daily ingestion cap must be 0.1 GB."
  }

  assert {
    condition     = azurerm_log_analytics_workspace.gateway.local_authentication_enabled == false
    error_message = "The workspace's local authentication must be off."
  }
}

run "application_insights" {
  command = plan

  assert {
    condition     = azurerm_application_insights.gateway.name == "appi-releaselens"
    error_message = "Application Insights must be named appi-releaselens."
  }

  assert {
    condition     = azurerm_application_insights.gateway.application_type == "other"
    error_message = "Application Insights' application type must be other."
  }

  assert {
    condition     = azurerm_application_insights.gateway.workspace_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.OperationalInsights/workspaces/log-releaselens"
    error_message = "Application Insights must be workspace-based, on the new workspace."
  }

  assert {
    condition     = azurerm_application_insights.gateway.local_authentication_enabled == false
    error_message = "Application Insights' local authentication must be off."
  }

  assert {
    condition     = azurerm_application_insights.gateway.resource_group_name == "rg-releaselens-bootstrap" && azurerm_application_insights.gateway.location == "australiaeast"
    error_message = "Application Insights must be in the bootstrap resource group, in the stack's location."
  }
}

run "metrics_publisher_role" {
  command = plan

  assert {
    condition     = azurerm_role_assignment.gateway_metrics_publisher.role_definition_name == "Monitoring Metrics Publisher" && azurerm_role_assignment.gateway_metrics_publisher.scope == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Insights/components/appi-releaselens" && azurerm_role_assignment.gateway_metrics_publisher.principal_id == "77777777-7777-7777-7777-777777777777"
    error_message = "gateway_metrics_publisher must give the gateway identity Monitoring Metrics Publisher on Application Insights only."
  }
}

# The gateway stack reads these through terraform_remote_state. The connection string holds an
# instrumentation key, so it is sensitive even though local authentication is off.
run "monitoring_outputs" {
  command = plan

  assert {
    condition     = output.app_insights_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Insights/components/appi-releaselens"
    error_message = "app_insights_id must be Application Insights' resource ID."
  }

  assert {
    condition     = output.app_insights_connection_string == "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://example.com/"
    error_message = "app_insights_connection_string must be Application Insights' connection string."
  }
}
