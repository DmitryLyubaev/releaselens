mock_provider "azurerm" {
  override_during = plan
}
mock_provider "random" {
  override_during = plan
  mock_resource "random_string" {
    defaults = { result = "a1b2c3" }
  }
}
override_resource {
  override_during = plan
  target          = azurerm_resource_group.platform
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-p8-platform" }
}
override_resource {
  override_during = plan
  target          = azurerm_resource_group.search
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-p8-search" }
}
override_resource {
  override_during = plan
  target          = azurerm_storage_account.platform
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-p8-platform/providers/Microsoft.Storage/storageAccounts/p8a1b2c3" }
}
override_resource {
  override_during = plan
  target          = azurerm_user_assigned_identity.delivery["test"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-p8-platform/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-p8-test", principal_id = "00000000-0000-0000-0000-000000000011", client_id = "00000000-0000-0000-0000-000000000021" }
}
override_resource {
  override_during = plan
  target          = azurerm_user_assigned_identity.delivery["prod"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-p8-platform/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-p8-prod", principal_id = "00000000-0000-0000-0000-000000000012", client_id = "00000000-0000-0000-0000-000000000022" }
}
override_resource {
  override_during = plan
  target          = azurerm_user_assigned_identity.delivery["verify"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-p8-platform/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-p8-verify", principal_id = "00000000-0000-0000-0000-000000000013", client_id = "00000000-0000-0000-0000-000000000023" }
}
override_resource {
  override_during = plan
  target          = azurerm_storage_container.private["inputs"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-p8-platform/providers/Microsoft.Storage/storageAccounts/p8a1b2c3/blobServices/default/containers/inputs" }
}
override_resource {
  override_during = plan
  target          = azurerm_storage_container.private["test-state"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-p8-platform/providers/Microsoft.Storage/storageAccounts/p8a1b2c3/blobServices/default/containers/test-state" }
}
override_resource {
  override_during = plan
  target          = azurerm_storage_container.private["prod-state"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-p8-platform/providers/Microsoft.Storage/storageAccounts/p8a1b2c3/blobServices/default/containers/prod-state" }
}
override_resource {
  override_during = plan
  target          = azurerm_storage_container.private["service-state"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-p8-platform/providers/Microsoft.Storage/storageAccounts/p8a1b2c3/blobServices/default/containers/service-state" }
}
override_resource {
  override_during = plan
  target          = azurerm_storage_container.private["evidence"]
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-p8-platform/providers/Microsoft.Storage/storageAccounts/p8a1b2c3/blobServices/default/containers/evidence" }
}
variables {
  subscription_id = "00000000-0000-0000-0000-000000000001"
  tenant_id       = "00000000-0000-0000-0000-000000000004"
  owner_object_id = "00000000-0000-0000-0000-000000000005"
  federation = {
    test   = { issuer = "https://issuer.example/current", subject = "sc://example/project/ReleaseLens-P8-Test" }
    prod   = { issuer = "https://issuer.example/current", subject = "sc://example/project/ReleaseLens-P8-Prod" }
    verify = { issuer = "https://issuer.example/current", subject = "sc://example/project/ReleaseLens-P8-Verify" }
  }
}
run "three_federated_identities" {
  command = plan
  assert {
    condition     = length(azurerm_user_assigned_identity.delivery) == 3 && toset([for i in values(azurerm_user_assigned_identity.delivery) : i.name]) == toset(["id-p8-test", "id-p8-prod", "id-p8-verify"]) && alltrue([for i in values(azurerm_user_assigned_identity.delivery) : i.resource_group_name == "rg-p8-platform"]) && azurerm_resource_group.platform.name == "rg-p8-platform"
    error_message = "Exactly the three long-lived identities, without a session slug, in rg-p8-platform."
  }
  assert {
    condition     = length(azurerm_federated_identity_credential.delivery) == 3 && alltrue([for k, c in azurerm_federated_identity_credential.delivery : c.audience == tolist(["api://AzureADTokenExchange"]) && c.issuer == var.federation[k].issuer && c.subject == var.federation[k].subject && c.user_assigned_identity_id == azurerm_user_assigned_identity.delivery[k].id])
    error_message = "Each identity trusts only its own service connection's issuer and subject."
  }
}
run "private_keyless_storage" {
  command = plan
  assert {
    condition     = !azurerm_storage_account.platform.shared_access_key_enabled && azurerm_storage_account.platform.min_tls_version == "TLS1_2" && !azurerm_storage_account.platform.allow_nested_items_to_be_public && azurerm_storage_account.platform.account_replication_type == "LRS" && azurerm_storage_account.platform.resource_group_name == "rg-p8-platform"
    error_message = "The platform account is LRS, keyless, TLS 1.2 and never public."
  }
  assert {
    condition     = toset(keys(azurerm_storage_container.private)) == toset(["inputs", "test-state", "prod-state", "service-state", "evidence"]) && alltrue([for c in values(azurerm_storage_container.private) : c.container_access_type == "private" && c.storage_account_id == azurerm_storage_account.platform.id])
    error_message = "Exactly the five approved containers, all private."
  }
  assert {
    condition     = azurerm_storage_account.platform.name == "p8a1b2c3" && can(regex("^[a-z0-9]{3,24}$", azurerm_storage_account.platform.name))
    error_message = "The account name is p8 plus the random suffix, a valid storage name."
  }
}
run "empty_search_group" {
  command = plan
  assert {
    condition     = azurerm_resource_group.search.name == "rg-p8-search" && azurerm_resource_group.search.location == "australiaeast"
    error_message = "The per-run search service lives in rg-p8-search."
  }
  assert {
    condition     = azurerm_storage_account.platform.resource_group_name != azurerm_resource_group.search.name && alltrue([for i in values(azurerm_user_assigned_identity.delivery) : i.resource_group_name != azurerm_resource_group.search.name])
    error_message = "Nothing long-lived may live in the search group; a run's teardown leaves it empty."
  }
}
run "grants_made_once" {
  command = plan
  assert {
    condition = length(azurerm_role_assignment.delivery) == 15 && toset(concat(
      [for a in values(azurerm_role_assignment.delivery) : "${lookup({ for r, i in azurerm_user_assigned_identity.delivery : i.principal_id => r }, a.principal_id, "unknown")}|${a.role_definition_name}|${regex("[^/]+$", a.scope)}"],
      ["${azurerm_role_assignment.owner_storage.principal_id == var.owner_object_id ? "owner" : "unknown"}|${azurerm_role_assignment.owner_storage.role_definition_name}|${regex("[^/]+$", azurerm_role_assignment.owner_storage.scope)}"]
      )) == toset([
      "test|Search Service Contributor|rg-p8-search",
      "test|Search Index Data Contributor|rg-p8-search",
      "prod|Search Service Contributor|rg-p8-search",
      "prod|Search Index Data Contributor|rg-p8-search",
      "verify|Search Index Data Reader|rg-p8-search",
      "verify|Reader|rg-p8-search",
      "test|Storage Blob Data Reader|inputs",
      "prod|Storage Blob Data Reader|inputs",
      "verify|Storage Blob Data Reader|inputs",
      "test|Storage Blob Data Contributor|evidence",
      "prod|Storage Blob Data Contributor|evidence",
      "verify|Storage Blob Data Contributor|evidence",
      "test|Storage Blob Data Contributor|test-state",
      "test|Storage Blob Data Contributor|service-state",
      "prod|Storage Blob Data Contributor|prod-state",
      "owner|Storage Blob Data Contributor|p8a1b2c3",
    ])
    error_message = "The grant matrix is exactly the approved one: no state cross-writes, verify read-only."
  }
  assert {
    condition     = alltrue([for a in values(azurerm_role_assignment.delivery) : a.scope == azurerm_resource_group.search.id if a.role_definition_name == "Reader" || startswith(a.role_definition_name, "Search ")])
    error_message = "Search roles are granted once on the group, never per service or index, so the negative stage can read prod as Test."
  }
  assert {
    condition     = azurerm_role_assignment.owner_storage.scope == azurerm_storage_account.platform.id && alltrue([for a in values(azurerm_role_assignment.delivery) : startswith(a.scope, azurerm_resource_group.search.id) || startswith(a.scope, "${azurerm_storage_account.platform.id}/blobServices/default/containers/")])
    error_message = "The owner is granted on the account; every other grant is on the search group or one container."
  }
  assert {
    condition     = alltrue([for s in concat([for a in values(azurerm_role_assignment.delivery) : a.scope], [azurerm_role_assignment.owner_storage.scope]) : can(regex("^/subscriptions/[^/]+/resourceGroups/[^/]+", s))])
    error_message = "No role assignment at subscription scope."
  }
}
run "outputs_for_the_variable_group" {
  command = plan
  assert {
    condition     = output.search_service_name == "p8-search-a1b2c3" && output.storage_account_name == "p8a1b2c3" && output.search_group == "rg-p8-search" && output.tenant_id == var.tenant_id && output.subscription_id == var.subscription_id
    error_message = "The settings the variable group is written from."
  }
  assert {
    condition = output.principals == {
      test   = { client_id = "00000000-0000-0000-0000-000000000021", object_id = "00000000-0000-0000-0000-000000000011" }
      prod   = { client_id = "00000000-0000-0000-0000-000000000022", object_id = "00000000-0000-0000-0000-000000000012" }
      verify = { client_id = "00000000-0000-0000-0000-000000000023", object_id = "00000000-0000-0000-0000-000000000013" }
    }
    error_message = "Each role's client and object IDs, for the expected principals."
  }
}
run "exactly_three_connections" {
  command = plan
  variables {
    federation = {
      test = { issuer = "https://issuer.example/current", subject = "sc://example/project/ReleaseLens-P8-Test" }
      prod = { issuer = "https://issuer.example/current", subject = "sc://example/project/ReleaseLens-P8-Prod" }
      ops  = { issuer = "https://issuer.example/current", subject = "sc://example/project/ReleaseLens-P8-Ops" }
    }
  }
  expect_failures = [var.federation]
}
