variables {
  subscription_id         = "00000000-0000-0000-0000-000000000000"
  tfstate_storage_account = "stexample"
}

# The three stacks' outputs, as placeholders: no state is read in a test. Each identifier is
# distinct, so a value wired to the wrong output fails its assertion.
override_data {
  target = data.terraform_remote_state.bootstrap
  values = {
    outputs = {
      tenant_id                        = "11111111-1111-1111-1111-111111111111"
      gateway_identity_client_id       = "22222222-2222-2222-2222-222222222222"
      gateway_app_client_id            = "33333333-3333-3333-3333-333333333333"
      azure_openai_base_url            = "https://aoai.example.com/openai/v1/"
      app_insights_connection_string   = "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://ingest.example.com/"
      ingest_identity_id               = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-ingest"
      ingest_identity_client_id        = "44444444-4444-4444-4444-444444444444"
      tool_identity_id                 = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-tool"
      tool_identity_client_id          = "55555555-5555-5555-5555-555555555555"
      ingest_storage_account_name      = "stingestexample"
      ingest_storage_blob_endpoint     = "https://stingestexample.blob.example.com/"
      ingest_storage_queue_endpoint    = "https://stingestexample.queue.example.com/"
      ingest_host_storage_account_name = "stingesthostexample"
      ingest_host_blob_endpoint        = "https://stingesthostexample.blob.example.com/"
      ingest_host_deploy_container     = "deploy-ingest"
      tool_host_storage_account_name   = "sttoolhostexample"
      tool_host_blob_endpoint          = "https://sttoolhostexample.blob.example.com/"
      tool_host_deploy_container       = "deploy-tool"
      search_tool_app_client_id        = "66666666-6666-6666-6666-666666666666"
      search_tool_app_identifier_uri   = "api://66666666-6666-6666-6666-666666666666"
    }
  }
}

override_data {
  target = data.terraform_remote_state.search
  values = {
    outputs = {
      endpoint = "https://search.example.com"
    }
  }
}

override_data {
  target = data.terraform_remote_state.gateway
  values = {
    outputs = {
      api_management_id      = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3"
      gateway_url            = "https://apim-releaselens-a1b2c3.gateway.example.com"
      app_insights_logger_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/loggers/appi-releaselens"
    }
  }
}

mock_provider "azurerm" {
  override_during = plan

  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-functions"
    }
  }

  mock_resource "azurerm_service_plan" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-functions/providers/Microsoft.Web/serverFarms/asp-releaselens-functions"
    }
  }
}

mock_provider "azapi" {
  override_during = plan
}

# The apps' names, and so their hosts, are built from the suffix, so it must be known at plan.
mock_provider "random" {
  override_during = plan

  mock_resource "random_string" {
    defaults = {
      result = "a1b2c3"
    }
  }
}

# Each app gets its own ID, and the tool app the host name the service reports, so the MCP API's
# backend and the authentication settings can be told apart by app.
override_resource {
  target          = azapi_resource.app["ingest"]
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-functions/providers/Microsoft.Web/sites/func-releaselens-ingest-a1b2c3"
    output = {
      properties = {
        defaultHostName = "func-releaselens-ingest-a1b2c3.sites.example.com"
      }
    }
  }
}

override_resource {
  target          = azapi_resource.app["tool"]
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-functions/providers/Microsoft.Web/sites/func-releaselens-tool-a1b2c3"
    output = {
      properties = {
        defaultHostName = "func-releaselens-tool-a1b2c3.sites.example.com"
      }
    }
  }
}

override_resource {
  target          = azapi_resource.mcp_api
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/releaselens-search"
  }
}

run "group_and_plan" {
  command = plan

  assert {
    condition     = azurerm_resource_group.functions.name == "rg-releaselens-functions" && azurerm_resource_group.functions.location == "australiaeast"
    error_message = "The resource group must be rg-releaselens-functions, in australiaeast."
  }

  assert {
    condition     = azurerm_service_plan.flex.sku_name == "FC1" && azurerm_service_plan.flex.os_type == "Linux" && azurerm_service_plan.flex.resource_group_name == "rg-releaselens-functions" && azurerm_service_plan.flex.location == "australiaeast"
    error_message = "The plan must be Flex Consumption (FC1), Linux, in rg-releaselens-functions, in australiaeast."
  }
}

run "apps" {
  command = plan

  assert {
    condition     = length(azapi_resource.app) == 2 && azapi_resource.app["ingest"].name == "func-releaselens-ingest-a1b2c3" && azapi_resource.app["tool"].name == "func-releaselens-tool-a1b2c3"
    error_message = "There must be two apps, func-releaselens-ingest- and func-releaselens-tool- followed by the stack's suffix."
  }

  assert {
    condition = alltrue([for app in azapi_resource.app :
      app.type == "Microsoft.Web/sites@2024-11-01" &&
      app.parent_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-functions" &&
      app.location == "australiaeast" &&
      app.body.kind == "functionapp,linux" &&
      app.body.properties.serverFarmId == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-functions/providers/Microsoft.Web/serverFarms/asp-releaselens-functions" &&
      app.body.properties.httpsOnly == true
    ])
    error_message = "Each app must be a Linux function app (Microsoft.Web/sites), on the Flex plan, in the group, HTTPS only."
  }

  assert {
    condition = alltrue([for app in azapi_resource.app :
      app.body.properties.functionAppConfig.runtime.name == "dotnet-isolated" &&
      app.body.properties.functionAppConfig.runtime.version == "10.0" &&
      app.body.properties.functionAppConfig.scaleAndConcurrency.instanceMemoryMB == 2048 &&
      length(app.body.properties.functionAppConfig.scaleAndConcurrency.alwaysReady) == 0
    ])
    error_message = "Each app must run .NET 10 isolated with 2,048 MB instances and no always-ready instance."
  }

  assert {
    condition = (
      azapi_resource.app["ingest"].identity[0].type == "UserAssigned" &&
      azapi_resource.app["ingest"].identity[0].identity_ids == tolist(["/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-ingest"]) &&
      azapi_resource.app["tool"].identity[0].type == "UserAssigned" &&
      azapi_resource.app["tool"].identity[0].identity_ids == tolist(["/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-tool"])
    )
    error_message = "Each app must have exactly its own identity, user-assigned: ingest the ingest identity, tool the tool identity."
  }

  assert {
    condition = (
      azapi_resource.app["ingest"].body.properties.functionAppConfig.deployment.storage.type == "blobContainer" &&
      azapi_resource.app["ingest"].body.properties.functionAppConfig.deployment.storage.value == "https://stingesthostexample.blob.example.com/deploy-ingest" &&
      azapi_resource.app["ingest"].body.properties.functionAppConfig.deployment.storage.authentication.type == "UserAssignedIdentity" &&
      azapi_resource.app["ingest"].body.properties.functionAppConfig.deployment.storage.authentication.userAssignedIdentityResourceId == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-ingest"
    )
    error_message = "The ingest app must deploy from deploy-ingest in its own host account, read with the ingest identity."
  }

  assert {
    condition = (
      azapi_resource.app["tool"].body.properties.functionAppConfig.deployment.storage.type == "blobContainer" &&
      azapi_resource.app["tool"].body.properties.functionAppConfig.deployment.storage.value == "https://sttoolhostexample.blob.example.com/deploy-tool" &&
      azapi_resource.app["tool"].body.properties.functionAppConfig.deployment.storage.authentication.type == "UserAssignedIdentity" &&
      azapi_resource.app["tool"].body.properties.functionAppConfig.deployment.storage.authentication.userAssignedIdentityResourceId == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-tool"
    )
    error_message = "The tool app must deploy from deploy-tool in its own host account, read with the tool identity."
  }
}

run "app_settings" {
  command = plan

  # Exactly these names: no AzureWebJobsStorage connection string, no deployment connection
  # string, and none of the settings Flex Consumption refuses.
  assert {
    condition = nonsensitive(toset([for s in azapi_resource.app["ingest"].body.properties.siteConfig.appSettings : s.name])) == toset([
      "AzureWebJobsStorage__accountName",
      "AzureWebJobsStorage__credential",
      "AzureWebJobsStorage__clientId",
      "AZURE_CLIENT_ID",
      "OPENAI_BASE_URL",
      "OPENAI_EMBEDDING_DEPLOYMENT",
      "SEARCH_ENDPOINT",
      "SEARCH_INDEX",
      "APPLICATIONINSIGHTS_CONNECTION_STRING",
      "APPLICATIONINSIGHTS_AUTHENTICATION_STRING",
      "IngestQueue__queueServiceUri",
      "IngestQueue__credential",
      "IngestQueue__clientId",
      "INGEST_BLOB_ENDPOINT",
    ])
    error_message = "The ingest app must have exactly its fourteen settings, and no AzureWebJobsStorage key."
  }

  assert {
    condition = nonsensitive(toset([for s in azapi_resource.app["tool"].body.properties.siteConfig.appSettings : s.name])) == toset([
      "AzureWebJobsStorage__accountName",
      "AzureWebJobsStorage__credential",
      "AzureWebJobsStorage__clientId",
      "AZURE_CLIENT_ID",
      "OPENAI_BASE_URL",
      "OPENAI_EMBEDDING_DEPLOYMENT",
      "SEARCH_ENDPOINT",
      "SEARCH_INDEX",
      "APPLICATIONINSIGHTS_CONNECTION_STRING",
      "APPLICATIONINSIGHTS_AUTHENTICATION_STRING",
    ])
    error_message = "The tool app must have exactly its ten settings, and no AzureWebJobsStorage key."
  }

  assert {
    condition = nonsensitive(
      { for s in azapi_resource.app["ingest"].body.properties.siteConfig.appSettings : s.name => s.value if s.name != "APPLICATIONINSIGHTS_CONNECTION_STRING" } == {
        AzureWebJobsStorage__accountName          = "stingesthostexample"
        AzureWebJobsStorage__credential           = "managedidentity"
        AzureWebJobsStorage__clientId             = "44444444-4444-4444-4444-444444444444"
        AZURE_CLIENT_ID                           = "44444444-4444-4444-4444-444444444444"
        OPENAI_BASE_URL                           = "https://aoai.example.com/openai/v1/"
        OPENAI_EMBEDDING_DEPLOYMENT               = "releaselens-embed-small"
        SEARCH_ENDPOINT                           = "https://search.example.com"
        SEARCH_INDEX                              = "releaselens-chunks"
        APPLICATIONINSIGHTS_AUTHENTICATION_STRING = "ClientId=44444444-4444-4444-4444-444444444444;Authorization=AAD"
        IngestQueue__queueServiceUri              = "https://stingestexample.queue.example.com"
        IngestQueue__credential                   = "managedidentity"
        IngestQueue__clientId                     = "44444444-4444-4444-4444-444444444444"
        INGEST_BLOB_ENDPOINT                      = "https://stingestexample.blob.example.com"
      }
    )
    error_message = "The ingest app's host storage must be its own host account as the ingest identity, and its queue and blobs the ingestion account."
  }

  assert {
    condition = nonsensitive(
      { for s in azapi_resource.app["tool"].body.properties.siteConfig.appSettings : s.name => s.value if s.name != "APPLICATIONINSIGHTS_CONNECTION_STRING" } == {
        AzureWebJobsStorage__accountName          = "sttoolhostexample"
        AzureWebJobsStorage__credential           = "managedidentity"
        AzureWebJobsStorage__clientId             = "55555555-5555-5555-5555-555555555555"
        AZURE_CLIENT_ID                           = "55555555-5555-5555-5555-555555555555"
        OPENAI_BASE_URL                           = "https://aoai.example.com/openai/v1/"
        OPENAI_EMBEDDING_DEPLOYMENT               = "releaselens-embed-small"
        SEARCH_ENDPOINT                           = "https://search.example.com"
        SEARCH_INDEX                              = "releaselens-chunks"
        APPLICATIONINSIGHTS_AUTHENTICATION_STRING = "ClientId=55555555-5555-5555-5555-555555555555;Authorization=AAD"
      }
    )
    error_message = "The tool app's host storage must be its own host account as the tool identity."
  }

  # The one sensitive setting: the value is the bootstrap's, and it is marked sensitive whatever
  # the source, so it never shows in a plan.
  assert {
    condition = alltrue([for app in azapi_resource.app : alltrue([
      for s in app.body.properties.siteConfig.appSettings :
      issensitive(s.value) && nonsensitive(s.value) == "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://ingest.example.com/"
      if s.name == "APPLICATIONINSIGHTS_CONNECTION_STRING"
    ])])
    error_message = "APPLICATIONINSIGHTS_CONNECTION_STRING must be the bootstrap's connection string, marked sensitive."
  }

  assert {
    condition = alltrue([for app in azapi_resource.app : alltrue([
      for s in app.body.properties.siteConfig.appSettings : !issensitive(s.value)
      if s.name != "APPLICATIONINSIGHTS_CONNECTION_STRING"
    ])])
    error_message = "No setting but the Application Insights connection string may be sensitive: the rest are identifiers and names."
  }
}

run "authentication" {
  command = plan

  assert {
    condition = (
      length(azapi_update_resource.auth) == 2 &&
      azapi_update_resource.auth["ingest"].resource_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-functions/providers/Microsoft.Web/sites/func-releaselens-ingest-a1b2c3/config/authsettingsV2" &&
      azapi_update_resource.auth["tool"].resource_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-functions/providers/Microsoft.Web/sites/func-releaselens-tool-a1b2c3/config/authsettingsV2" &&
      alltrue([for auth in azapi_update_resource.auth : auth.type == "Microsoft.Web/sites/config@2024-11-01"])
    )
    error_message = "Each app must have its own authsettingsV2."
  }

  assert {
    condition = alltrue([for auth in azapi_update_resource.auth :
      auth.body.properties.platform.enabled == true &&
      auth.body.properties.globalValidation.requireAuthentication == true &&
      auth.body.properties.globalValidation.unauthenticatedClientAction == "Return401" &&
      auth.body.properties.httpSettings.requireHttps == true
    ])
    error_message = "Each app must require authentication on every route, answer 401 without it, and require HTTPS."
  }

  assert {
    condition = alltrue([for auth in azapi_update_resource.auth :
      auth.body.properties.identityProviders.azureActiveDirectory.enabled == true &&
      auth.body.properties.identityProviders.azureActiveDirectory.registration.openIdIssuer == "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111/v2.0" &&
      auth.body.properties.identityProviders.azureActiveDirectory.registration.clientId == "66666666-6666-6666-6666-666666666666"
    ])
    error_message = "Each app must trust Entra ID for the tenant's v2 issuer, as the search tool's registration."
  }

  assert {
    condition = alltrue([for auth in azapi_update_resource.auth :
      toset(auth.body.properties.identityProviders.azureActiveDirectory.validation.allowedAudiences) == toset(["api://66666666-6666-6666-6666-666666666666", "66666666-6666-6666-6666-666666666666"]) &&
      auth.body.properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedApplications == ["22222222-2222-2222-2222-222222222222"]
    ])
    error_message = "Each app must accept the search tool's audience in both forms, from the gateway identity alone."
  }
}

# Basic publishing credentials are a password: off for SCM and FTP on both apps, so code deploys
# with the owner's Entra sign-in alone.
run "basic_publishing_credentials" {
  command = plan

  assert {
    condition = length(azapi_update_resource.basic_publishing) == 4 && alltrue([
      for app in ["ingest", "tool"] : alltrue([
        for kind in ["scm", "ftp"] :
        azapi_update_resource.basic_publishing["${app}-${kind}"].type == "Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01" &&
        azapi_update_resource.basic_publishing["${app}-${kind}"].resource_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-functions/providers/Microsoft.Web/sites/func-releaselens-${app}-a1b2c3/basicPublishingCredentialsPolicies/${kind}" &&
        azapi_update_resource.basic_publishing["${app}-${kind}"].body.properties.allow == false
      ])
    ])
    error_message = "Each app must have basic publishing credentials off for both scm and ftp."
  }
}

run "mcp_api" {
  command = plan

  assert {
    condition = (
      azapi_resource.mcp_api.type == "Microsoft.ApiManagement/service/apis@2025-09-01-preview" &&
      azapi_resource.mcp_api.name == "releaselens-search" &&
      azapi_resource.mcp_api.parent_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3"
    )
    error_message = "The MCP API must be an API of the gateway's service, at 2025-09-01-preview."
  }

  assert {
    condition = (
      azapi_resource.mcp_api.body.properties.type == "mcp" &&
      azapi_resource.mcp_api.body.properties.path == "releaselens-search" &&
      azapi_resource.mcp_api.body.properties.protocols == ["https"] &&
      azapi_resource.mcp_api.body.properties.subscriptionRequired == false
    )
    error_message = "The MCP API must be of type mcp, at path releaselens-search, HTTPS only, with no subscription key."
  }

  assert {
    condition = (
      azapi_resource.mcp_api.body.properties.serviceUrl == "https://func-releaselens-tool-a1b2c3.sites.example.com/runtime/webhooks" &&
      azapi_resource.mcp_api.body.properties.mcpProperties.transportType == "streamable" &&
      length(azapi_resource.mcp_api.body.properties.mcpProperties.endpoints) == 1 &&
      azapi_resource.mcp_api.body.properties.mcpProperties.endpoints[0].name == "message" &&
      azapi_resource.mcp_api.body.properties.mcpProperties.endpoints[0].uriTemplate == "/mcp"
    )
    error_message = "The MCP API must pass Streamable HTTP through to the tool app's /runtime/webhooks/mcp."
  }

  assert {
    condition = (
      azapi_resource.mcp_policy.type == "Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview" &&
      azapi_resource.mcp_policy.name == "policy" &&
      azapi_resource.mcp_policy.parent_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/releaselens-search"
    )
    error_message = "The policy must be the MCP API's own."
  }

  assert {
    condition     = azapi_resource.mcp_policy.body.properties.format == "xml" && azapi_resource.mcp_policy.body.properties.value == file("policies/mcp-api.xml")
    error_message = "The MCP API's policy must be policies/mcp-api.xml, unchanged, in the xml format."
  }
}

run "named_values" {
  command = plan

  assert {
    condition     = length(azapi_resource.named_value) == 4
    error_message = "There must be exactly the four named values the MCP policy uses."
  }

  assert {
    condition = (
      azapi_resource.named_value["tool-tenant-id"].body.properties.value == "11111111-1111-1111-1111-111111111111" &&
      azapi_resource.named_value["tool-gateway-app-client-id"].body.properties.value == "33333333-3333-3333-3333-333333333333" &&
      azapi_resource.named_value["tool-app-audience"].body.properties.value == "api://66666666-6666-6666-6666-666666666666" &&
      azapi_resource.named_value["tool-gateway-identity-client-id"].body.properties.value == "22222222-2222-2222-2222-222222222222"
    )
    error_message = "Each named value must come from the bootstrap's state: the tenant, the gateway app, the search tool's audience and the gateway identity."
  }

  assert {
    condition = alltrue([for name, value in azapi_resource.named_value :
      value.type == "Microsoft.ApiManagement/service/namedValues@2024-05-01" &&
      value.parent_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3" &&
      value.name == name &&
      value.body.properties.displayName == name &&
      value.body.properties.secret == false
    ])
    error_message = "No named value is secret, and each is on the gateway's service, named and displayed as the policy refers to it."
  }
}

run "mcp_diagnostic" {
  command = plan

  assert {
    condition = (
      azapi_resource.mcp_diagnostic.type == "Microsoft.ApiManagement/service/apis/diagnostics@2025-09-01-preview" &&
      azapi_resource.mcp_diagnostic.name == "applicationinsights" &&
      azapi_resource.mcp_diagnostic.parent_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/releaselens-search" &&
      azapi_resource.mcp_diagnostic.body.properties.loggerId == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/loggers/appi-releaselens"
    )
    error_message = "The MCP API must have an Application Insights diagnostic through the gateway's logger."
  }

  assert {
    condition = (
      azapi_resource.mcp_diagnostic.body.properties.sampling.samplingType == "fixed" &&
      azapi_resource.mcp_diagnostic.body.properties.sampling.percentage == 100 &&
      azapi_resource.mcp_diagnostic.body.properties.logClientIp == false &&
      azapi_resource.mcp_diagnostic.body.properties.metrics == true
    )
    error_message = "The diagnostic must sample every request, log no client IP, and have metrics on, so emit-metric publishes."
  }

  assert {
    condition = (
      azapi_resource.mcp_diagnostic.body.properties.frontend.request.body.bytes == 0 &&
      azapi_resource.mcp_diagnostic.body.properties.frontend.response.body.bytes == 0 &&
      azapi_resource.mcp_diagnostic.body.properties.backend.request.body.bytes == 0 &&
      azapi_resource.mcp_diagnostic.body.properties.backend.response.body.bytes == 0 &&
      azapi_resource.mcp_diagnostic.body.properties.largeLanguageModel.logs == "disabled"
    )
    error_message = "The diagnostic must log no payload, in front or behind, so Streamable HTTP is never buffered."
  }
}

run "outputs" {
  command = plan

  assert {
    condition     = output.ingest_app_name == "func-releaselens-ingest-a1b2c3" && output.tool_app_name == "func-releaselens-tool-a1b2c3"
    error_message = "ingest_app_name and tool_app_name must be the apps' names."
  }

  assert {
    condition     = output.tool_app_url == "https://func-releaselens-tool-a1b2c3.sites.example.com/runtime/webhooks/mcp"
    error_message = "tool_app_url must be the tool app's own Streamable HTTP endpoint."
  }

  assert {
    condition     = output.tool_mcp_url == "https://apim-releaselens-a1b2c3.gateway.example.com/releaselens-search/mcp"
    error_message = "tool_mcp_url must be the gateway's MCP endpoint for the tool: https://<gateway host>/releaselens-search/mcp."
  }
}
