variables {
  subscription_id    = "00000000-0000-0000-0000-000000000000"
  budget_alert_email = "owner@example.com"
  budget_start_date  = "2026-10-01T00:00:00Z"
}

# The gateway's Entra app is part of the stack, so every test plans it. The data source's result
# needs the Azure CLI's key.
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
}

mock_provider "random" {}

# The mock default above gives every resource group the same id. The app group gets its
# own, so a lock scoped to the wrong group fails the scope assertion instead of passing.
override_resource {
  target          = azurerm_resource_group.app
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens"
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

run "groups_and_budget" {
  command = plan

  assert {
    condition     = azurerm_resource_group.bootstrap.name == "rg-releaselens-bootstrap" && azurerm_resource_group.bootstrap.location == "australiaeast"
    error_message = "The bootstrap group must be rg-releaselens-bootstrap in australiaeast."
  }

  assert {
    condition     = azurerm_resource_group.app.name == "rg-releaselens" && azurerm_resource_group.app.location == "australiaeast"
    error_message = "The app group must be rg-releaselens in australiaeast."
  }

  assert {
    condition     = azurerm_consumption_budget_subscription.this.amount == 50 && azurerm_consumption_budget_subscription.this.time_grain == "Monthly"
    error_message = "The budget must be 50 a month."
  }

  assert {
    condition = toset([
      for n in azurerm_consumption_budget_subscription.this.notification : n.threshold if n.threshold_type == "Actual"
    ]) == toset([50, 80, 100])
    error_message = "The actual-spend alerts must be exactly 50, 80 and 100 percent."
  }

  assert {
    condition = [
      for n in azurerm_consumption_budget_subscription.this.notification : n.threshold if n.threshold_type == "Forecasted"
    ] == [100]
    error_message = "There must be exactly one forecast alert, at 100 percent."
  }

  assert {
    condition     = azurerm_monitor_action_group.budget.resource_group_name == "rg-releaselens-bootstrap"
    error_message = "The budget's action group must be in the bootstrap group."
  }

  assert {
    condition     = azurerm_management_lock.bootstrap.lock_level == "CanNotDelete"
    error_message = "The bootstrap group's lock must be CanNotDelete."
  }

  assert {
    condition     = azurerm_management_lock.bootstrap.scope == azurerm_resource_group.bootstrap.id
    error_message = "The lock must be scoped to the bootstrap group."
  }
}
