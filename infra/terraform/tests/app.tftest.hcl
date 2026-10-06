variables {
  subscription_id         = "00000000-0000-0000-0000-000000000000"
  app_identity_id         = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-app"
  app_identity_client_id  = "11111111-1111-1111-1111-111111111111"
  azure_openai_base_url   = "https://aoai-releaselens-a1b2c3.openai.azure.com/openai/v1/"
  azure_openai_deployment = "releaselens-chat"
}

mock_provider "azurerm" {
  override_during = plan
}

mock_provider "random" {}

# image is set per run rather than above, so that destroy_placeholder_is_valid can leave it
# unset.
run "deploys_only_app_resources" {
  command = plan

  variables {
    image = "ghcr.io/dmitrylyubaev/releaselens-api@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }

  assert {
    condition     = azurerm_container_app.api.identity[0].type == "UserAssigned" && azurerm_container_app.api.identity[0].identity_ids == toset(["/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-app"])
    error_message = "The Container App must run as exactly one identity, the user-assigned app identity it is given."
  }

  # The map is compared whole, so an environment variable that is missing, has the wrong
  # value or is not in the table fails it. RELEASELENS_DB, which comes from a secret, has no
  # value and is checked below.
  assert {
    condition = tomap({ for env in azurerm_container_app.api.template[0].container[0].env : env.name => env.value if env.name != "RELEASELENS_DB" }) == tomap({
      ASPNETCORE_ENVIRONMENT      = "Production"
      Chat__Providers__0          = "azure-openai"
      AzureOpenAi__BaseUrl        = "https://aoai-releaselens-a1b2c3.openai.azure.com/openai/v1/"
      AzureOpenAi__Deployment     = "releaselens-chat"
      AzureOpenAi__Credential     = "ManagedIdentity"
      AzureOpenAi__Model          = "gpt-4.1-mini"
      AzureOpenAi__ModelVersion   = "2025-04-14"
      AzureOpenAi__DeploymentType = "GlobalStandard"
      AzureOpenAi__TokenScope     = "https://ai.azure.com/.default"
      AZURE_CLIENT_ID             = "11111111-1111-1111-1111-111111111111"
    })
    error_message = "The container's environment variables must be exactly the ones in the table, with their values."
  }

  assert {
    condition     = { for env in azurerm_container_app.api.template[0].container[0].env : env.name => env }["RELEASELENS_DB"].secret_name == "database-connection"
    error_message = "RELEASELENS_DB must come from the secret database-connection."
  }

  assert {
    condition     = length([for env in azurerm_container_app.api.template[0].container[0].env : env.name if contains(["ANTHROPIC_API_KEY", "OPENAI_API_KEY"], env.name)]) == 0
    error_message = "The container must have no ANTHROPIC_API_KEY or OPENAI_API_KEY environment variable."
  }

  assert {
    condition     = azurerm_container_app.api.template[0].container[0].image == "ghcr.io/dmitrylyubaev/releaselens-api@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    error_message = "The container's image must be the image given."
  }

  assert {
    condition     = length(azurerm_postgresql_flexible_server_firewall_rule.smoke_runner) == 0
    error_message = "With no smoke runner IP set, no runner firewall rule may exist."
  }

  assert {
    condition     = azurerm_container_app_environment.this.log_analytics_workspace_id == null
    error_message = "The Container Apps environment must have no Log Analytics workspace."
  }

  assert {
    condition     = azurerm_postgresql_flexible_server.this.resource_group_name == "rg-releaselens"
    error_message = "The Postgres server must be in rg-releaselens."
  }
}

# Gateway mode is configuration only: the app is pointed at the gateway by azure_openai_base_url,
# and asks Entra for a token for the gateway app instead of the model account.
run "token_scope_passes_through" {
  command = plan

  variables {
    image                    = "ghcr.io/dmitrylyubaev/releaselens-api@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    azure_openai_token_scope = "api://11111111-1111-1111-1111-111111111111/.default"
  }

  assert {
    condition     = { for env in azurerm_container_app.api.template[0].container[0].env : env.name => env.value if env.name == "AzureOpenAi__TokenScope" }["AzureOpenAi__TokenScope"] == "api://11111111-1111-1111-1111-111111111111/.default"
    error_message = "AzureOpenAi__TokenScope must be the azure_openai_token_scope given."
  }
}

run "runner_rule_when_ip_set" {
  command = plan

  variables {
    image           = "ghcr.io/dmitrylyubaev/releaselens-api@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    smoke_runner_ip = "203.0.113.10"
  }

  assert {
    condition     = length(azurerm_postgresql_flexible_server_firewall_rule.smoke_runner) == 1
    error_message = "With a smoke runner IP set, exactly one runner firewall rule must exist."
  }

  assert {
    condition     = azurerm_postgresql_flexible_server_firewall_rule.smoke_runner[0].start_ip_address == "203.0.113.10" && azurerm_postgresql_flexible_server_firewall_rule.smoke_runner[0].end_ip_address == "203.0.113.10"
    error_message = "The runner firewall rule must open exactly the IP given."
  }
}

run "latest_tag_rejected" {
  command = plan

  variables {
    image = "ghcr.io/dmitrylyubaev/releaselens-api:latest"
  }

  expect_failures = [var.image]
}

# The destroy workflow has no image to deploy, so the default must pass validation.
run "destroy_placeholder_is_valid" {
  command = plan

  assert {
    condition     = azurerm_container_app.api.template[0].container[0].image == "ghcr.io/dmitrylyubaev/releaselens-api@sha256:0000000000000000000000000000000000000000000000000000000000000000"
    error_message = "With no image given, the container's image must be the all-zero digest placeholder."
  }
}
