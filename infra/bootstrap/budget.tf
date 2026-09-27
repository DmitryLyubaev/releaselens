# In the long-lived stack, so the subscription keeps its budget while the app stack is
# destroyed. Azure has no hard spending cap on a pay-as-you-go subscription, so this is
# alerting, not enforcement.

resource "azurerm_monitor_action_group" "budget" {
  name                = "ag-releaselens-budget"
  resource_group_name = azurerm_resource_group.bootstrap.name
  short_name          = "rlbudget"

  email_receiver {
    name          = "owner"
    email_address = var.budget_alert_email
  }
}

resource "azurerm_consumption_budget_subscription" "this" {
  name            = "budget-releaselens-monthly"
  subscription_id = "/subscriptions/${var.subscription_id}"

  amount     = var.budget_amount_usd
  time_grain = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  dynamic "notification" {
    for_each = [50, 80, 100]

    content {
      enabled        = true
      threshold      = notification.value
      operator       = "GreaterThanOrEqualTo"
      threshold_type = "Actual"
      contact_emails = [var.budget_alert_email]
      contact_groups = [azurerm_monitor_action_group.budget.id]
    }
  }

  # Forecast alert: warns before the money is spent rather than after.
  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Forecasted"
    contact_emails = [var.budget_alert_email]
    contact_groups = [azurerm_monitor_action_group.budget.id]
  }
}
