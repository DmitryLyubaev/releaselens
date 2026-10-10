# A short-lived stack: the owner applies it in a functions session and destroys it at the end
# (spec §3.3, §9), so nothing here has prevent_destroy. Nothing in it bills while idle: Flex
# Consumption has no always-ready instance here, so an app costs only while it runs.

# A group of its own, not rg-releaselens: there, the nightly destroy's empty-group check would
# find the apps and fail.
resource "azurerm_resource_group" "functions" {
  name     = "rg-releaselens-functions"
  location = var.location
}

# The apps' names are their hosts, so they must be unique across Azure. A new suffix each
# session, as the gateway's service has.
resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

# One Flex Consumption plan for both apps. azurerm's plan resource is safe here: only its
# function app resource injects the storage connection string.
resource "azurerm_service_plan" "flex" {
  name                = "asp-releaselens-functions"
  resource_group_name = azurerm_resource_group.functions.name
  location            = azurerm_resource_group.functions.location
  os_type             = "Linux"
  sku_name            = "FC1"
}

locals {
  # Each app's identity and host account, from the bootstrap. Each app's host storage and
  # deployment container are in a host account of its own, which its identity alone has host
  # roles on, so neither app can reach the other's host files or the artefacts through them.
  apps = {
    ingest = {
      identity_id        = local.bootstrap.ingest_identity_id
      client_id          = local.bootstrap.ingest_identity_client_id
      host_account       = local.bootstrap.ingest_host_storage_account_name
      host_blob_endpoint = local.bootstrap.ingest_host_blob_endpoint
      deploy_container   = local.bootstrap.ingest_host_deploy_container
    }
    tool = {
      identity_id        = local.bootstrap.tool_identity_id
      client_id          = local.bootstrap.tool_identity_client_id
      host_account       = local.bootstrap.tool_host_storage_account_name
      host_blob_endpoint = local.bootstrap.tool_host_blob_endpoint
      deploy_container   = local.bootstrap.tool_host_deploy_container
    }
  }

  # The settings both apps read (src/ReleaseLens.Functions.*/*Settings.cs), as each app's own
  # identity. The host storage is identity-based: there is no AzureWebJobsStorage connection
  # string, which would take precedence over these and fail on accounts with shared keys off.
  # Application Insights takes Entra ingestion (local authentication is off there); its
  # connection string carries the instrumentation key, so it is the one sensitive value, marked
  # here whatever the source.
  common_settings = { for app, config in local.apps : app => {
    AzureWebJobsStorage__accountName          = config.host_account
    AzureWebJobsStorage__credential           = "managedidentity"
    AzureWebJobsStorage__clientId             = config.client_id
    AZURE_CLIENT_ID                           = config.client_id
    OPENAI_BASE_URL                           = local.bootstrap.azure_openai_base_url
    OPENAI_EMBEDDING_DEPLOYMENT               = "releaselens-embed-small"
    SEARCH_ENDPOINT                           = local.search.endpoint
    SEARCH_INDEX                              = "releaselens-chunks"
    APPLICATIONINSIGHTS_CONNECTION_STRING     = sensitive(local.bootstrap.app_insights_connection_string)
    APPLICATIONINSIGHTS_AUTHENTICATION_STRING = "ClientId=${config.client_id};Authorization=AAD"
  } }

  # The ingest app's queue trigger (Connection = "IngestQueue") and its blob reads go to the
  # ingestion account, not its host account, as the ingest identity. The bootstrap's endpoints
  # end in a slash; the settings take the bare service URI.
  extra_settings = {
    ingest = {
      IngestQueue__queueServiceUri = trimsuffix(local.bootstrap.ingest_storage_queue_endpoint, "/")
      IngestQueue__credential      = "managedidentity"
      IngestQueue__clientId        = local.apps.ingest.client_id
      INGEST_BLOB_ENDPOINT         = trimsuffix(local.bootstrap.ingest_storage_blob_endpoint, "/")
    }
    tool = {}
  }

  # merge keeps the connection string's sensitive mark on that value alone, so a plan shows
  # every other setting.
  app_settings = { for app in keys(local.apps) : app => [
    for name, value in merge(local.common_settings[app], local.extra_settings[app]) : {
      name  = name
      value = value
    }
  ] }
}

# The apps are Microsoft.Web/sites through azapi, in the shape of Microsoft's Flex MCP sample
# (research notes §2g): azurerm_function_app_flex_consumption would inject AzureWebJobsStorage.
# No FUNCTIONS_WORKER_RUNTIME or WEBSITE_RUN_FROM_PACKAGE: Flex refuses both, and takes the
# runtime from functionAppConfig.
resource "azapi_resource" "app" {
  for_each = local.apps

  type      = "Microsoft.Web/sites@2024-11-01"
  name      = "func-releaselens-${each.key}-${random_string.suffix.result}"
  parent_id = azurerm_resource_group.functions.id
  location  = azurerm_resource_group.functions.location

  # Its own identity, and no system-assigned one: every role it needs is the bootstrap's, granted
  # to this identity before the session.
  identity {
    type         = "UserAssigned"
    identity_ids = [each.value.identity_id]
  }

  body = {
    kind = "functionapp,linux"
    properties = {
      serverFarmId = azurerm_service_plan.flex.id
      httpsOnly    = true
      siteConfig = {
        minTlsVersion = "1.2"
        ftpsState     = "Disabled"
        appSettings   = local.app_settings[each.key]
      }
      functionAppConfig = {
        # The package is read from the app's own container, as its own identity, which holds
        # Storage Blob Data Owner on its host account. No deployment connection string.
        deployment = {
          storage = {
            type  = "blobContainer"
            value = "${each.value.host_blob_endpoint}${each.value.deploy_container}"
            authentication = {
              type                           = "UserAssignedIdentity"
              userAssignedIdentityResourceId = each.value.identity_id
            }
          }
        }
        # No always-ready instance: one would bill about US$26 a month idle (research notes
        # §8). The first call after idle waits for a cold start. maximumInstanceCount is left to
        # the service's default.
        scaleAndConcurrency = {
          instanceMemoryMB = 2048
          alwaysReady      = []
        }
        runtime = {
          name    = "dotnet-isolated"
          version = "10.0"
        }
      }
    }
  }

  # The tool app's host, for the MCP API's backend and the outputs, as the service reports it.
  response_export_values = ["properties.defaultHostName"]
}

# App Service authentication on every route of both apps (spec §3.3): a call without a token
# from the gateway identity for the search tool's registration is a 401 before it reaches the
# Functions host, so the MCP endpoint (whose extension key is off, webhookAuthorizationLevel =
# "Anonymous") and the runtime's own key-authenticated endpoints are unreachable without it.
# The ingest app has no HTTP function; the setting closes its runtime endpoints too.
#
# An update of the site's authsettingsV2 rather than a resource of its own: a site's config
# cannot be deleted on its own, and deleting the site removes it.
resource "azapi_update_resource" "auth" {
  for_each = local.apps

  type        = "Microsoft.Web/sites/config@2024-11-01"
  resource_id = "${azapi_resource.app[each.key].id}/config/authsettingsV2"

  body = {
    properties = {
      platform = {
        enabled = true
      }
      globalValidation = {
        requireAuthentication       = true
        unauthenticatedClientAction = "Return401"
      }
      httpSettings = {
        requireHttps = true
      }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            # The tenant's v2 issuer: the registration issues v2 tokens.
            openIdIssuer = "https://login.microsoftonline.com/${local.bootstrap.tenant_id}/v2.0"
            clientId     = local.bootstrap.search_tool_app_client_id
          }
          validation = {
            # A v2 token's audience is the client ID; the identifier URI is the form the gateway
            # asks for. Both are the same registration.
            allowedAudiences = [
              local.bootstrap.search_tool_app_identifier_uri,
              local.bootstrap.search_tool_app_client_id,
            ]
            # The gateway identity alone: a token the registration issued to anyone else is
            # refused, whatever its audience.
            defaultAuthorizationPolicy = {
              allowedApplications = [local.bootstrap.gateway_identity_client_id]
            }
          }
        }
      }
    }
  }
}

# Basic publishing credentials are a username and password for the SCM (Kudu) and FTP endpoints.
# Both are off on both apps, so code deploys only with the owner's Entra sign-in. Updates rather
# than resources of their own, as for authsettingsV2: the policies cannot be deleted on their
# own, and deleting the site removes them.
locals {
  basic_publishing = {
    for pair in setproduct(keys(local.apps), ["scm", "ftp"]) : "${pair[0]}-${pair[1]}" => {
      app  = pair[0]
      kind = pair[1]
    }
  }
}

resource "azapi_update_resource" "basic_publishing" {
  for_each = local.basic_publishing

  type        = "Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01"
  resource_id = "${azapi_resource.app[each.value.app].id}/basicPublishingCredentialsPolicies/${each.value.kind}"

  body = {
    properties = {
      allow = false
    }
  }
}
