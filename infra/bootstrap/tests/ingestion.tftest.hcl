variables {
  subscription_id    = "00000000-0000-0000-0000-000000000000"
  budget_alert_email = "owner@example.com"
  budget_start_date  = "2026-10-01T00:00:00Z"
}

# The ingestion storage account, its containers and queues, the Event Grid system topic and its
# one subscription, and the two Function identities (spec §3.1). The state account and the
# ingestion account get distinct IDs, as do the two containers and the two queues the
# subscription names, so a subscription pointed at the wrong one fails its assertion.
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

  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap"
    }
  }

  mock_resource "azurerm_storage_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlstatea1b2c3"
    }
  }

  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-deploy"
      principal_id = "33333333-3333-3333-3333-333333333333"
      client_id    = "44444444-4444-4444-4444-444444444444"
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
  target          = azurerm_storage_account.ingest
  override_during = plan
  values = {
    id                    = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlingesta1b2c3"
    primary_blob_endpoint = "https://strlingesta1b2c3.blob.core.windows.net/"
    # A different form from the blob endpoint's, so an output wired to the wrong one fails.
    primary_queue_endpoint = "https://strlingesta1b2c3.queue.core.windows.net/"
  }
}

# Each app's host account has its own ID and endpoint, so a container or output wired to the wrong
# account fails.
override_resource {
  target          = azurerm_storage_account.ingest_host
  override_during = plan
  values = {
    id                    = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlingesthosta1b2c3"
    primary_blob_endpoint = "https://strlingesthosta1b2c3.blob.core.windows.net/"
  }
}

override_resource {
  target          = azurerm_storage_account.tool_host
  override_during = plan
  values = {
    id                    = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strltoolhosta1b2c3"
    primary_blob_endpoint = "https://strltoolhosta1b2c3.blob.core.windows.net/"
  }
}

override_resource {
  target          = azurerm_storage_container.deadletter_events
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlingesta1b2c3/blobServices/default/containers/deadletter-events"
  }
}

override_resource {
  target          = azurerm_eventgrid_system_topic.ingest
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.EventGrid/systemTopics/evgt-releaselens-ingest"
  }
}

override_resource {
  target          = azurerm_user_assigned_identity.ingest
  override_during = plan
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-ingest"
    principal_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    client_id    = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
  }
}

override_resource {
  target          = azurerm_user_assigned_identity.tool
  override_during = plan
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-tool"
    principal_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    client_id    = "dddddddd-dddd-dddd-dddd-dddddddddddd"
  }
}

# The owner is whoever applies the stack: the signed-in principal.
override_data {
  target = data.azurerm_client_config.current
  values = {
    object_id = "22222222-2222-2222-2222-222222222222"
    tenant_id = "11111111-1111-1111-1111-111111111111"
  }
}

run "ingestion_account" {
  command = plan

  # The ingestion account holds the artefacts and the queues; each app's host storage and
  # deployment package are in an account of its own, so neither app's host roles reach the
  # artefacts or the other app (spec §3.1, as amended).
  assert {
    condition = (
      azurerm_storage_account.ingest_host.name == "strlingesthosta1b2c3" &&
      azurerm_storage_account.tool_host.name == "strltoolhosta1b2c3" &&
      length(azurerm_storage_account.ingest_host.name) <= 24 &&
      length(azurerm_storage_account.tool_host.name) <= 24
    )
    error_message = "The host accounts must be strlingesthost<suffix> and strltoolhost<suffix>, within the 24-character limit."
  }

  assert {
    condition = alltrue([
      for a in [
        azurerm_storage_account.ingest,
        azurerm_storage_account.ingest_host,
        azurerm_storage_account.tool_host,
        ] : (
        a.shared_access_key_enabled == false &&
        a.default_to_oauth_authentication == true &&
        a.local_user_enabled == false &&
        a.allow_nested_items_to_be_public == false &&
        a.min_tls_version == "TLS1_2" &&
        a.account_tier == "Standard" && a.account_replication_type == "LRS" &&
        a.resource_group_name == "rg-releaselens-bootstrap" && a.location == "australiaeast"
      )
    ])
    error_message = "All three accounts must be keyless (shared keys and local users off, OAuth by default), with no public blob access, TLS 1.2, Standard LRS, in the bootstrap group in australiaeast."
  }

  assert {
    condition     = azurerm_storage_account.ingest.name == "strlingesta1b2c3"
    error_message = "The ingestion account must be named strlingest followed by the existing suffix."
  }

  assert {
    condition     = azurerm_storage_account.ingest.resource_group_name == "rg-releaselens-bootstrap" && azurerm_storage_account.ingest.location == "australiaeast"
    error_message = "The ingestion account must be in the bootstrap group, in australiaeast."
  }

  assert {
    condition     = azurerm_storage_account.ingest.account_tier == "Standard" && azurerm_storage_account.ingest.account_replication_type == "LRS"
    error_message = "The ingestion account must be Standard LRS."
  }

  assert {
    condition     = azurerm_storage_account.ingest.shared_access_key_enabled == false
    error_message = "The ingestion account's shared keys must be off."
  }

  assert {
    condition     = azurerm_storage_account.ingest.default_to_oauth_authentication == true
    error_message = "The ingestion account must default to OAuth authentication."
  }

  assert {
    condition     = azurerm_storage_account.ingest.local_user_enabled == false
    error_message = "The ingestion account's local users must be off."
  }

  assert {
    condition     = azurerm_storage_account.ingest.allow_nested_items_to_be_public == false
    error_message = "The ingestion account must not allow public blob access."
  }

  assert {
    condition     = azurerm_storage_account.ingest.min_tls_version == "TLS1_2"
    error_message = "The ingestion account's minimum TLS version must be 1.2."
  }
}

run "ingestion_containers_and_queues" {
  command = plan

  assert {
    condition = alltrue([
      for c in [
        azurerm_storage_container.artefacts_in,
        azurerm_storage_container.deadletter_events,
        azurerm_storage_container.deploy_ingest,
        azurerm_storage_container.deploy_tool,
      ] : c.container_access_type == "private"
    ])
    error_message = "Every ingestion container must be private."
  }

  assert {
    condition = (
      azurerm_storage_container.artefacts_in.storage_account_id == azurerm_storage_account.ingest.id &&
      azurerm_storage_container.deadletter_events.storage_account_id == azurerm_storage_account.ingest.id
    )
    error_message = "artefacts-in and deadletter-events must be in the ingestion account."
  }

  assert {
    condition     = azurerm_storage_container.deploy_ingest.storage_account_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlingesthosta1b2c3"
    error_message = "deploy-ingest must be in the ingest app's host account."
  }

  assert {
    condition     = azurerm_storage_container.deploy_tool.storage_account_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strltoolhosta1b2c3"
    error_message = "deploy-tool must be in the tool app's host account."
  }

  assert {
    condition = [
      azurerm_storage_container.artefacts_in.name,
      azurerm_storage_container.deadletter_events.name,
      azurerm_storage_container.deploy_ingest.name,
      azurerm_storage_container.deploy_tool.name,
    ] == ["artefacts-in", "deadletter-events", "deploy-ingest", "deploy-tool"]
    error_message = "The containers must be artefacts-in, deadletter-events, deploy-ingest and deploy-tool."
  }

  assert {
    condition     = azurerm_storage_queue.ingest_events.name == "ingest-events" && azurerm_storage_queue.ingest_events.storage_account_id == azurerm_storage_account.ingest.id
    error_message = "The ingest-events queue must be in the ingestion account."
  }

  # Pre-created, so the ingest identity's role can be scoped to it before the runtime writes the
  # first poison message.
  assert {
    condition     = azurerm_storage_queue.ingest_events_poison.name == "ingest-events-poison" && azurerm_storage_queue.ingest_events_poison.storage_account_id == azurerm_storage_account.ingest.id
    error_message = "The ingest-events-poison queue must be in the ingestion account."
  }

  # The state account is a different account: nothing of the ingestion lands in it.
  assert {
    condition     = length(toset([azurerm_storage_account.state.id, azurerm_storage_account.ingest.id, azurerm_storage_account.ingest_host.id, azurerm_storage_account.tool_host.id])) == 4
    error_message = "The state, ingestion and two host accounts must be four different accounts."
  }
}

run "event_grid_topic" {
  command = plan

  assert {
    condition     = azurerm_eventgrid_system_topic.ingest.name == "evgt-releaselens-ingest" && azurerm_eventgrid_system_topic.ingest.resource_group_name == "rg-releaselens-bootstrap" && azurerm_eventgrid_system_topic.ingest.location == "australiaeast"
    error_message = "The system topic must be evgt-releaselens-ingest, in the bootstrap group, in the account's region."
  }

  assert {
    condition     = azurerm_eventgrid_system_topic.ingest.source_resource_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlingesta1b2c3" && azurerm_eventgrid_system_topic.ingest.topic_type == "Microsoft.Storage.StorageAccounts"
    error_message = "The system topic must be the ingestion account's storage topic."
  }

  assert {
    condition     = length(azurerm_eventgrid_system_topic.ingest.identity) == 1 && azurerm_eventgrid_system_topic.ingest.identity[0].type == "SystemAssigned"
    error_message = "The system topic must have a system-assigned identity, and only that."
  }
}

run "event_grid_subscription" {
  command = plan

  assert {
    condition     = azurerm_eventgrid_system_topic_event_subscription.artefacts.system_topic == "evgt-releaselens-ingest" && azurerm_eventgrid_system_topic_event_subscription.artefacts.resource_group_name == "rg-releaselens-bootstrap"
    error_message = "The subscription must be on the ingestion account's system topic."
  }

  assert {
    condition     = azurerm_eventgrid_system_topic_event_subscription.artefacts.included_event_types == tolist(["Microsoft.Storage.BlobCreated"])
    error_message = "The subscription must carry Microsoft.Storage.BlobCreated only."
  }

  assert {
    condition = (
      length(azurerm_eventgrid_system_topic_event_subscription.artefacts.subject_filter) == 1 &&
      azurerm_eventgrid_system_topic_event_subscription.artefacts.subject_filter[0].subject_begins_with == "/blobServices/default/containers/artefacts-in/" &&
      azurerm_eventgrid_system_topic_event_subscription.artefacts.subject_filter[0].subject_ends_with == ".json"
    )
    error_message = "The subject filter must begin with the artefacts-in container and end with .json."
  }

  assert {
    condition     = length(azurerm_eventgrid_system_topic_event_subscription.artefacts.advanced_filter) == 0
    error_message = "The subscription must have no advanced filter: the subject filter alone selects the events."
  }

  assert {
    condition = (
      length(azurerm_eventgrid_system_topic_event_subscription.artefacts.storage_queue_endpoint) == 1 &&
      azurerm_eventgrid_system_topic_event_subscription.artefacts.storage_queue_endpoint[0].storage_account_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlingesta1b2c3" &&
      azurerm_eventgrid_system_topic_event_subscription.artefacts.storage_queue_endpoint[0].queue_name == "ingest-events"
    )
    error_message = "The subscription must deliver to the ingest-events queue in the ingestion account."
  }

  # Delivery with the topic's identity, not a key: there is no other endpoint and no key in the
  # endpoint.
  assert {
    condition = (
      length(azurerm_eventgrid_system_topic_event_subscription.artefacts.delivery_identity) == 1 &&
      azurerm_eventgrid_system_topic_event_subscription.artefacts.delivery_identity[0].type == "SystemAssigned" &&
      length(azurerm_eventgrid_system_topic_event_subscription.artefacts.webhook_endpoint) == 0 &&
      length(azurerm_eventgrid_system_topic_event_subscription.artefacts.azure_function_endpoint) == 0
    )
    error_message = "The subscription must deliver with the topic's system-assigned identity, to the queue only."
  }

  assert {
    condition = (
      length(azurerm_eventgrid_system_topic_event_subscription.artefacts.storage_blob_dead_letter_destination) == 1 &&
      azurerm_eventgrid_system_topic_event_subscription.artefacts.storage_blob_dead_letter_destination[0].storage_account_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Storage/storageAccounts/strlingesta1b2c3" &&
      azurerm_eventgrid_system_topic_event_subscription.artefacts.storage_blob_dead_letter_destination[0].storage_blob_container_name == "deadletter-events"
    )
    error_message = "The subscription must dead-letter to the deadletter-events container in the ingestion account."
  }

  assert {
    condition = (
      length(azurerm_eventgrid_system_topic_event_subscription.artefacts.dead_letter_identity) == 1 &&
      azurerm_eventgrid_system_topic_event_subscription.artefacts.dead_letter_identity[0].type == "SystemAssigned"
    )
    error_message = "The subscription must dead-letter with the topic's system-assigned identity."
  }
}

run "function_identities" {
  command = plan

  assert {
    condition     = azurerm_user_assigned_identity.ingest.name == "id-releaselens-ingest" && azurerm_user_assigned_identity.ingest.resource_group_name == "rg-releaselens-bootstrap" && azurerm_user_assigned_identity.ingest.location == "australiaeast"
    error_message = "The ingest identity must be id-releaselens-ingest, in the bootstrap group, in australiaeast."
  }

  assert {
    condition     = azurerm_user_assigned_identity.tool.name == "id-releaselens-tool" && azurerm_user_assigned_identity.tool.resource_group_name == "rg-releaselens-bootstrap" && azurerm_user_assigned_identity.tool.location == "australiaeast"
    error_message = "The tool identity must be id-releaselens-tool, in the bootstrap group, in australiaeast."
  }
}

# Read by the functions stack (Tasks 6 and 7). Identifiers and endpoints only: no key, no
# connection string, no SAS.
run "ingestion_outputs" {
  command = plan

  assert {
    condition = (
      output.ingest_identity_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-ingest" &&
      output.ingest_identity_client_id == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" &&
      output.ingest_identity_principal_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )
    error_message = "The ingest identity's outputs must be its resource ID, client ID and principal ID."
  }

  assert {
    condition = (
      output.tool_identity_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-tool" &&
      output.tool_identity_client_id == "dddddddd-dddd-dddd-dddd-dddddddddddd" &&
      output.tool_identity_principal_id == "cccccccc-cccc-cccc-cccc-cccccccccccc"
    )
    error_message = "The tool identity's outputs must be its resource ID, client ID and principal ID."
  }

  assert {
    condition = (
      output.ingest_storage_account_name == "strlingesta1b2c3" &&
      output.ingest_storage_blob_endpoint == "https://strlingesta1b2c3.blob.core.windows.net/" &&
      output.ingest_storage_queue_endpoint == "https://strlingesta1b2c3.queue.core.windows.net/"
    )
    error_message = "The ingestion account's outputs must be its name and its blob and queue endpoints."
  }

  assert {
    condition = (
      output.ingest_host_storage_account_name == "strlingesthosta1b2c3" &&
      output.ingest_host_blob_endpoint == "https://strlingesthosta1b2c3.blob.core.windows.net/" &&
      output.ingest_host_deploy_container == "deploy-ingest"
    )
    error_message = "The ingest app's host outputs must be its host account's name and blob endpoint, and its deploy container's name."
  }

  assert {
    condition = (
      output.tool_host_storage_account_name == "strltoolhosta1b2c3" &&
      output.tool_host_blob_endpoint == "https://strltoolhosta1b2c3.blob.core.windows.net/" &&
      output.tool_host_deploy_container == "deploy-tool"
    )
    error_message = "The tool app's host outputs must be its host account's name and blob endpoint, and its deploy container's name."
  }
}

# Review focus 5: the additions leave everything already deployed as it was, so the bootstrap
# plan changes nothing on it.
run "existing_resources_unchanged" {
  command = plan

  assert {
    condition = (
      azurerm_storage_account.state.name == "strlstatea1b2c3" &&
      azurerm_storage_account.state.shared_access_key_enabled == false &&
      azurerm_storage_account.state.local_user_enabled == false &&
      azurerm_storage_account.state.min_tls_version == "TLS1_2" &&
      azurerm_storage_account.state.blob_properties[0].versioning_enabled == true
    )
    error_message = "The state account must still be strlstate<suffix>, keyless, with versioning on."
  }

  assert {
    condition = [
      azurerm_storage_container.bootstrap.name,
      azurerm_storage_container.app.name,
      azurerm_storage_container.search.name,
      azurerm_storage_container.gateway.name,
    ] == ["tfstate-bootstrap", "tfstate-app", "tfstate-search", "tfstate-gateway"]
    error_message = "The existing state containers must keep their names."
  }

  assert {
    condition = (
      azurerm_cognitive_account.openai.name == "aoai-releaselens-a1b2c3" &&
      azurerm_cognitive_account.openai.kind == "AIServices" &&
      azurerm_cognitive_account.openai.local_auth_enabled == false
    )
    error_message = "The primary Azure OpenAI account must still be aoai-releaselens-<suffix>, AIServices, keyless."
  }

  assert {
    condition = (
      azurerm_cognitive_deployment.chat.name == "releaselens-chat" &&
      azurerm_cognitive_deployment.chat.model[0].name == "gpt-4.1-mini" &&
      azurerm_cognitive_deployment.chat.model[0].version == "2025-04-14" &&
      azurerm_cognitive_deployment.chat.sku[0].capacity == 300 &&
      azurerm_cognitive_deployment.embedding_small.name == "releaselens-embed-small" &&
      azurerm_cognitive_deployment.embedding_small.model[0].name == "text-embedding-3-small" &&
      azurerm_cognitive_deployment.embedding_small.model[0].version == "1" &&
      azurerm_cognitive_deployment.embedding_large.name == "releaselens-embed-large" &&
      azurerm_cognitive_deployment.embedding_large.model[0].name == "text-embedding-3-large"
    )
    error_message = "The chat and embedding deployments must keep their names, models, versions and capacity."
  }

  assert {
    condition = (
      azurerm_user_assigned_identity.deploy.name == "id-releaselens-deploy" &&
      azurerm_user_assigned_identity.app.name == "id-releaselens-app" &&
      azurerm_user_assigned_identity.gateway.name == "id-releaselens-gateway" &&
      alltrue([
        for i in [
          azurerm_user_assigned_identity.deploy,
          azurerm_user_assigned_identity.app,
          azurerm_user_assigned_identity.gateway,
        ] : i.resource_group_name == "rg-releaselens-bootstrap" && i.location == "australiaeast"
      ])
    )
    error_message = "The deploy, app and gateway identities must keep their names, group and region."
  }

  assert {
    condition = (
      azuread_application.gateway.display_name == "releaselens-ai-gateway" &&
      length(azuread_application.gateway.app_role) == 1 &&
      alltrue([for r in azuread_application.gateway.app_role : r.value == "Gateway.Invoke" && r.allowed_member_types == toset(["User", "Application"])]) &&
      azuread_application.gateway.api[0].requested_access_token_version == 2 &&
      azuread_service_principal.gateway.app_role_assignment_required == true
    )
    error_message = "The gateway's app must keep its name, its one role Gateway.Invoke, version 2 tokens, and its required assignment."
  }

  assert {
    condition = (
      azurerm_log_analytics_workspace.gateway.name == "log-releaselens" &&
      azurerm_log_analytics_workspace.gateway.retention_in_days == 30 &&
      azurerm_log_analytics_workspace.gateway.daily_quota_gb == 0.1 &&
      azurerm_log_analytics_workspace.gateway.local_authentication_enabled == false &&
      azurerm_application_insights.gateway.name == "appi-releaselens" &&
      azurerm_application_insights.gateway.local_authentication_enabled == false &&
      azapi_update_resource.appi_custom_metrics.body.properties.CustomMetricsOptedInType == "WithDimensions"
    )
    error_message = "Monitoring must keep its names, retention, daily cap, keyless authentication and custom metrics with dimensions."
  }
}
