variables {
  subscription_id    = "00000000-0000-0000-0000-000000000000"
  budget_alert_email = "owner@example.com"
  budget_start_date  = "2026-10-01T00:00:00Z"
}

mock_provider "azurerm" {
  override_during = plan

  mock_resource "azurerm_storage_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3"
    }
  }
}

# The account's name is built from the suffix, so the suffix must be known at plan.
mock_provider "random" {
  override_during = plan

  mock_resource "random_string" {
    defaults = {
      result = "a1b2c3"
    }
  }
}

run "state_storage" {
  command = plan

  assert {
    condition     = azurerm_storage_account.state.name == "strlstatea1b2c3"
    error_message = "The state account must be named strlstate followed by the suffix."
  }

  assert {
    condition     = azurerm_storage_account.state.resource_group_name == "rg-releaselens-bootstrap" && azurerm_storage_account.state.location == "australiaeast"
    error_message = "The state account must be in the bootstrap group, in australiaeast."
  }

  assert {
    condition     = azurerm_storage_account.state.account_tier == "Standard" && azurerm_storage_account.state.account_replication_type == "LRS"
    error_message = "The state account must be Standard LRS."
  }

  assert {
    condition     = azurerm_storage_account.state.shared_access_key_enabled == false
    error_message = "The state account's shared keys must be off."
  }

  assert {
    condition     = azurerm_storage_account.state.default_to_oauth_authentication == true
    error_message = "The state account must default to OAuth authentication."
  }

  assert {
    condition     = azurerm_storage_account.state.local_user_enabled == false
    error_message = "The state account's local users must be off."
  }

  assert {
    condition     = azurerm_storage_account.state.allow_nested_items_to_be_public == false
    error_message = "The state account must not allow nested items to be public."
  }

  assert {
    condition     = azurerm_storage_account.state.min_tls_version == "TLS1_2"
    error_message = "The state account's minimum TLS version must be 1.2."
  }

  assert {
    condition     = azurerm_storage_account.state.blob_properties[0].versioning_enabled == true
    error_message = "Blob versioning must be on."
  }

  assert {
    condition     = azurerm_storage_account.state.blob_properties[0].delete_retention_policy[0].days == 7
    error_message = "Blob soft delete must keep deleted blobs for 7 days."
  }

  assert {
    condition     = azurerm_storage_account.state.blob_properties[0].container_delete_retention_policy[0].days == 7
    error_message = "Container soft delete must keep deleted containers for 7 days."
  }

  assert {
    condition     = azurerm_storage_container.bootstrap.name == "tfstate-bootstrap" && azurerm_storage_container.bootstrap.container_access_type == "private"
    error_message = "The bootstrap state container must be tfstate-bootstrap, and private."
  }

  assert {
    condition     = azurerm_storage_container.app.name == "tfstate-app" && azurerm_storage_container.app.container_access_type == "private"
    error_message = "The app state container must be tfstate-app, and private."
  }

  assert {
    condition     = azurerm_storage_container.search.name == "tfstate-search" && azurerm_storage_container.search.container_access_type == "private"
    error_message = "The search state container must be tfstate-search, and private."
  }

  assert {
    condition     = azurerm_storage_container.bootstrap.storage_account_id == azurerm_storage_account.state.id
    error_message = "The bootstrap state container must be in the state account."
  }

  assert {
    condition     = azurerm_storage_container.app.storage_account_id == azurerm_storage_account.state.id
    error_message = "The app state container must be in the state account."
  }

  assert {
    condition     = azurerm_storage_container.search.storage_account_id == azurerm_storage_account.state.id
    error_message = "The search state container must be in the state account."
  }

  assert {
    condition     = azurerm_storage_management_policy.state.storage_account_id == azurerm_storage_account.state.id
    error_message = "The lifecycle policy must be on the state account."
  }

  assert {
    condition     = length(azurerm_storage_management_policy.state.rule) == 1 && azurerm_storage_management_policy.state.rule[0].enabled
    error_message = "The lifecycle policy must have exactly one rule, enabled."
  }

  assert {
    condition     = azurerm_storage_management_policy.state.rule[0].actions[0].version[0].delete_after_days_since_creation == 90
    error_message = "The lifecycle rule must delete blob versions 90 days after creation."
  }

  assert {
    condition     = length(azurerm_storage_management_policy.state.rule[0].actions[0].base_blob) == 0
    error_message = "The lifecycle rule must act only on versions, never on the current state blob."
  }

  assert {
    condition     = azurerm_storage_management_policy.state.rule[0].filters[0].prefix_match == null
    error_message = "The lifecycle rule must cover every state container, with no prefix filter."
  }
}

run "gateway_state_container" {
  command = plan

  assert {
    condition     = azurerm_storage_container.gateway.name == "tfstate-gateway" && azurerm_storage_container.gateway.container_access_type == "private"
    error_message = "The gateway state container must be tfstate-gateway, and private."
  }

  assert {
    condition     = azurerm_storage_container.gateway.storage_account_id == azurerm_storage_account.state.id
    error_message = "The gateway state container must be in the state account."
  }
}
