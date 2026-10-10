# Ingestion's storage and Event Grid delivery (spec §3.1). Long-lived, because an upload made while
# no session is running must wait in the queue for the next one, and nothing here bills by the
# hour. The Function apps themselves are per session (infra/functions).
#
# Three accounts. The ingestion account holds the artefacts, the dead-letter container and the two
# queues. Each Function app's host storage (AzureWebJobsStorage) and deployment package are in an
# account of its own: the host needs Storage Blob Data Owner on its whole account, and in a shared
# account that role would let each app write artefacts-in, and so the index, and the other app's
# package.
#
# Keyless throughout: shared keys and local users are off on all three, so every data-plane
# request, the apps' host storage included, is authorised through Entra ID; and Event Grid writes
# to the queue and the dead-letter container as the topic's own identity.

resource "azurerm_storage_account" "ingest" {
  name                     = "strlingest${random_string.suffix.result}"
  resource_group_name      = azurerm_resource_group.bootstrap.name
  location                 = azurerm_resource_group.bootstrap.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  # azurerm 5.x still defaults this to true.
  local_user_enabled = false
  # Written out, as on the state account, rather than left to the provider default.
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"

  tags = local.tags
}

# The demo and the export upload artefacts here, as <artefact>.json. Every BlobCreated event in it
# for a .json blob becomes one queue message.
resource "azurerm_storage_container" "artefacts_in" {
  name                  = "artefacts-in"
  storage_account_id    = azurerm_storage_account.ingest.id
  container_access_type = "private"
}

# Events that Event Grid itself cannot deliver to the queue (spec §4.5). Without it they are
# dropped.
resource "azurerm_storage_container" "deadletter_events" {
  name                  = "deadletter-events"
  storage_account_id    = azurerm_storage_account.ingest.id
  container_access_type = "private"
}

resource "azurerm_storage_queue" "ingest_events" {
  name               = "ingest-events"
  storage_account_id = azurerm_storage_account.ingest.id
}

# The runtime moves a message here after its third failed try (host.json maxDequeueCount). It is
# created here, not by the runtime, so the ingest identity's role can be scoped to it.
resource "azurerm_storage_queue" "ingest_events_poison" {
  name               = "ingest-events-poison"
  storage_account_id = azurerm_storage_account.ingest.id
}

# One system topic per storage account is allowed. Its system-assigned identity is the one Event
# Grid delivers and dead-letters with; a system-assigned identity is also the only kind that works
# if the account is ever firewalled.
resource "azurerm_eventgrid_system_topic" "ingest" {
  name                = "evgt-releaselens-ingest"
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location
  source_resource_id  = azurerm_storage_account.ingest.id
  topic_type          = "Microsoft.Storage.StorageAccounts"

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

resource "azurerm_eventgrid_system_topic_event_subscription" "artefacts" {
  name                = "artefacts-in-to-ingest-events"
  system_topic        = azurerm_eventgrid_system_topic.ingest.name
  resource_group_name = azurerm_resource_group.bootstrap.name

  included_event_types = ["Microsoft.Storage.BlobCreated"]

  subject_filter {
    subject_begins_with = "/blobServices/default/containers/artefacts-in/"
    subject_ends_with   = ".json"
  }

  storage_queue_endpoint {
    storage_account_id = azurerm_storage_account.ingest.id
    queue_name         = azurerm_storage_queue.ingest_events.name
  }

  delivery_identity {
    type = "SystemAssigned"
  }

  storage_blob_dead_letter_destination {
    storage_account_id          = azurerm_storage_account.ingest.id
    storage_blob_container_name = azurerm_storage_container.deadletter_events.name
  }

  dead_letter_identity {
    type = "SystemAssigned"
  }

  # The provider's note: the delivery identity must hold its roles before the subscription is
  # created. The roles are in roles.tf, with every other assignment.
  depends_on = [
    azurerm_role_assignment.eventgrid_queue_sender,
    azurerm_role_assignment.eventgrid_deadletter_writer,
  ]
}

# The ingest app's host account: its AzureWebJobsStorage and its deployment package. 20
# characters with the suffix, within the 24-character limit.
resource "azurerm_storage_account" "ingest_host" {
  name                     = "strlingesthost${random_string.suffix.result}"
  resource_group_name      = azurerm_resource_group.bootstrap.name
  location                 = azurerm_resource_group.bootstrap.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  local_user_enabled              = false
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"

  tags = local.tags
}

# The search tool's host account, likewise. 18 characters with the suffix.
resource "azurerm_storage_account" "tool_host" {
  name                     = "strltoolhost${random_string.suffix.result}"
  resource_group_name      = azurerm_resource_group.bootstrap.name
  location                 = azurerm_resource_group.bootstrap.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  local_user_enabled              = false
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"

  tags = local.tags
}

# One deployment-package container per app, in that app's host account: Flex Consumption needs it
# to exist before the app is created.
resource "azurerm_storage_container" "deploy_ingest" {
  name                  = "deploy-ingest"
  storage_account_id    = azurerm_storage_account.ingest_host.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "deploy_tool" {
  name                  = "deploy-tool"
  storage_account_id    = azurerm_storage_account.tool_host.id
  container_access_type = "private"
}
