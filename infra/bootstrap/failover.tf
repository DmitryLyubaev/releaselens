# The AI gateway's two backends (spec §3.1). The first account is in openai.tf. This is the
# second, in another region, so a regional fault or a quota throttle on the first has somewhere
# to fail over to. Nothing here bills by the hour: Global Standard deployments bill per token.
locals {
  failover_account_name = "aoai-releaselens-sea-${random_string.suffix.result}"

  # The failover-test deployment's name, in one place. The gateway stack reads it through the
  # failover_test_deployment output.
  failover_test_deployment_name = "releaselens-chat-failover-test"
}

# Australia Southeast is not used: it does not offer Global Standard for this model (spec §3.1).
# Kind AIServices for the same reason as the first account (see openai.tf).
resource "azurerm_cognitive_account" "failover" {
  name                = local.failover_account_name
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = var.failover_location
  kind                = "AIServices"
  sku_name            = "S0"

  # Entra ID authentication needs a custom subdomain, and changing it replaces the account.
  custom_subdomain_name = local.failover_account_name

  # No key exists for the gateway to hold: every call is authorised through Entra ID, so the
  # gateway's managed identity is the only way in. azurerm defaults this to true.
  local_auth_enabled = false
  # azurerm defaults this to false. It is written out because, once it is on, turning it off
  # again replaces the account.
  project_management_enabled = false
}

# The same name as the first account's chat deployment, so one backend pool serves both: the
# request names the deployment in its body, and the same body works on either account
# (spec §3.1). Capacity is 100 by default.
resource "azurerm_cognitive_deployment" "failover_chat" {
  name                 = var.azure_openai_deployment_name
  cognitive_account_id = azurerm_cognitive_account.failover.id

  model {
    format  = "OpenAI"
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.failover_capacity
  }

  # Pinned, as on the first account (spec §3.1).
  version_upgrade_option = "NoAutoUpgrade"
}

# The failover test sends its requests to this deployment name on both accounts, so nothing is
# re-applied between a failover run and a normal one (spec §3.1). On this account it has the
# same capacity as releaselens-chat: it is the healthy side of the test.
resource "azurerm_cognitive_deployment" "failover_test" {
  name                 = local.failover_test_deployment_name
  cognitive_account_id = azurerm_cognitive_account.failover.id

  model {
    format  = "OpenAI"
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.failover_capacity
  }

  version_upgrade_option = "NoAutoUpgrade"
}

# The australiaeast side of the failover test, deliberately tiny. Capacity 1 is 1,000 tokens a
# minute, so a handful of requests throttle it with a 429 on purpose, and the gateway fails over
# to the Southeast Asia deployment of the same name (spec §3.1). It carries no real traffic.
resource "azurerm_cognitive_deployment" "primary_failover_test" {
  name                 = local.failover_test_deployment_name
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 1
  }

  version_upgrade_option = "NoAutoUpgrade"
}
