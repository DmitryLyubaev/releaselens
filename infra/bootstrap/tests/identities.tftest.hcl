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

# The Application Insights custom-metrics switch (monitoring.tf) is an azapi update; mocked here
# like every other provider, so no test reaches Azure.
mock_provider "azapi" {}

mock_provider "azurerm" {
  override_during = plan

  # The principal ID goes to the gateway's Gateway.Invoke assignments, which the provider checks
  # for a UUID.
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-deploy"
      principal_id = "33333333-3333-3333-3333-333333333333"
    }
  }
}

mock_provider "random" {}

# The mock default above gives both identities the same id. The app identity gets its own,
# so a credential parented to the wrong identity fails the parent assertion instead of
# passing.
override_resource {
  target          = azurerm_user_assigned_identity.app
  override_during = plan
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-app"
    principal_id = "55555555-5555-5555-5555-555555555555"
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

# The budget-only apply (R5) runs before the subject is known, so an unset subject must pass
# validation and create no credential.
run "no_subject_no_credential" {
  command = plan

  assert {
    condition     = length(azurerm_federated_identity_credential.github_environment) == 0
    error_message = "With no subject set, no federated credential may exist."
  }

  assert {
    condition     = azurerm_user_assigned_identity.deploy.name == "id-releaselens-deploy" && azurerm_user_assigned_identity.deploy.resource_group_name == "rg-releaselens-bootstrap" && azurerm_user_assigned_identity.deploy.location == "australiaeast"
    error_message = "The deploy identity must be id-releaselens-deploy, in the bootstrap group, in australiaeast."
  }

  assert {
    condition     = azurerm_user_assigned_identity.app.name == "id-releaselens-app" && azurerm_user_assigned_identity.app.resource_group_name == "rg-releaselens-bootstrap" && azurerm_user_assigned_identity.app.location == "australiaeast"
    error_message = "The app identity must be id-releaselens-app, in the bootstrap group, in australiaeast."
  }
}

run "subject_creates_one_credential" {
  command = plan

  variables {
    github_oidc_subject = "repo:DmitryLyubaev@57339946/releaselens@1331560542:environment:azure"
  }

  assert {
    condition     = length(azurerm_federated_identity_credential.github_environment) == 1
    error_message = "With a subject set, exactly one federated credential must exist."
  }

  assert {
    condition     = azurerm_federated_identity_credential.github_environment[0].name == "github-environment-azure"
    error_message = "The federated credential must be named github-environment-azure."
  }

  assert {
    condition     = azurerm_federated_identity_credential.github_environment[0].issuer == "https://token.actions.githubusercontent.com"
    error_message = "The federated credential's issuer must be GitHub Actions' token issuer."
  }

  assert {
    condition     = azurerm_federated_identity_credential.github_environment[0].audience == tolist(["api://AzureADTokenExchange"])
    error_message = "The federated credential's audience must be exactly api://AzureADTokenExchange."
  }

  assert {
    condition     = azurerm_federated_identity_credential.github_environment[0].subject == "repo:DmitryLyubaev@57339946/releaselens@1331560542:environment:azure"
    error_message = "The federated credential's subject must be exactly the value given."
  }

  assert {
    condition     = azurerm_federated_identity_credential.github_environment[0].user_assigned_identity_id == azurerm_user_assigned_identity.deploy.id
    error_message = "The federated credential must be on the deploy identity."
  }
}

run "branch_subject_rejected" {
  command = plan

  variables {
    github_oidc_subject = "repo:DmitryLyubaev@57339946/releaselens@1331560542:ref:refs/heads/main"
  }

  expect_failures = [var.github_oidc_subject]
}

run "other_repository_rejected" {
  command = plan

  variables {
    github_oidc_subject = "repo:someone/else:environment:azure"
  }

  expect_failures = [var.github_oidc_subject]
}

# Each run below breaks exactly one rule, so deleting or weakening that rule fails its run.

# Only the environment rule rejects this one.
run "pull_request_subject_rejected" {
  command = plan

  variables {
    github_oidc_subject = "repo:DmitryLyubaev@57339946/releaselens@1331560542:pull_request"
  }

  expect_failures = [var.github_oidc_subject]
}

# Only the ref: rule rejects this one.
run "ref_in_environment_subject_rejected" {
  command = plan

  variables {
    github_oidc_subject = "repo:DmitryLyubaev@57339946/releaselens@1331560542:environment:azure:ref:refs/heads/main"
  }

  expect_failures = [var.github_oidc_subject]
}

# Only the environment rule's anchoring rejects this one: a plain substring match accepts it.
run "other_environment_rejected" {
  command = plan

  variables {
    github_oidc_subject = "repo:DmitryLyubaev@57339946/releaselens@1331560542:environment:azure-staging"
  }

  expect_failures = [var.github_oidc_subject]
}
