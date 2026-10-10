variables {
  subscription_id         = "00000000-0000-0000-0000-000000000000"
  tfstate_storage_account = "stexample"
  publisher_email         = "owner@example.com"
}

# The bootstrap's outputs, as placeholders: the state is never read in a test. Each identifier
# is distinct, so a value wired to the wrong output fails its assertion.
override_data {
  target = data.terraform_remote_state.bootstrap
  values = {
    outputs = {
      tenant_id                      = "11111111-1111-1111-1111-111111111111"
      gateway_identity_id            = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-gateway"
      gateway_identity_client_id     = "22222222-2222-2222-2222-222222222222"
      gateway_app_client_id          = "33333333-3333-3333-3333-333333333333"
      primary_openai_backend_url     = "https://aoai-primary.example.com/openai/v1"
      failover_openai_backend_url    = "https://aoai-secondary.example.com/openai/v1"
      failover_test_deployment       = "releaselens-chat-failover-test"
      app_insights_id                = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Insights/components/appi-releaselens"
      app_insights_connection_string = "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://ingest.example.com/"
    }
  }
}

mock_provider "azurerm" {
  override_during = plan

  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway"
    }
  }

  mock_resource "azurerm_api_management" {
    defaults = {
      id          = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3"
      gateway_url = "https://apim-releaselens-a1b2c3.azure-api.net"
    }
  }

  mock_resource "azurerm_api_management_api_version_set" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apiVersionSets/azure-openai"
    }
  }

  mock_resource "azurerm_api_management_api" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/azure-openai-v1;rev=1"
    }
  }

  mock_resource "azurerm_api_management_logger" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/loggers/appi-releaselens"
    }
  }

  mock_resource "azurerm_api_management_api_diagnostic" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/azure-openai-v1/diagnostics/applicationinsights"
    }
  }
}

mock_provider "azapi" {
  override_during = plan
}

# The service's name, and so its host, are built from the suffix, so the suffix must be known at
# plan.
mock_provider "random" {
  override_during = plan

  mock_resource "random_string" {
    defaults = {
      result = "a1b2c3"
    }
  }
}

# The mock default gives both revisions the same id. Revision 2 gets its own, so a release or a
# source pointing at the wrong revision fails its assertion.
override_resource {
  target          = azurerm_api_management_api.v1_rev2
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/azure-openai-v1;rev=2"
  }
}

# The same for the backends, so the pool's members can be told apart.
override_resource {
  target          = azapi_resource.backend["aoai-primary"]
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/backends/aoai-primary"
  }
}

override_resource {
  target          = azapi_resource.backend["aoai-secondary"]
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/backends/aoai-secondary"
  }
}

run "service" {
  command = plan

  assert {
    condition     = azurerm_resource_group.gateway.name == "rg-releaselens-gateway" && azurerm_resource_group.gateway.location == "australiaeast"
    error_message = "The resource group must be rg-releaselens-gateway, in australiaeast."
  }

  assert {
    condition     = azurerm_api_management.gateway.name == "apim-releaselens-a1b2c3" && startswith(azurerm_api_management.gateway.name, "apim-releaselens-")
    error_message = "API Management must be named apim-releaselens- followed by the stack's random suffix."
  }

  assert {
    condition     = azurerm_api_management.gateway.sku_name == "BasicV2_1" && azurerm_api_management.gateway.location == "australiaeast" && azurerm_api_management.gateway.resource_group_name == "rg-releaselens-gateway"
    error_message = "API Management must be BasicV2_1, in rg-releaselens-gateway, in australiaeast."
  }

  assert {
    condition     = azurerm_api_management.gateway.publisher_email == "owner@example.com"
    error_message = "The publisher email must come from publisher_email."
  }

  assert {
    condition     = azurerm_api_management.gateway.identity[0].type == "UserAssigned" && azurerm_api_management.gateway.identity[0].identity_ids == toset(["/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-releaselens-gateway"])
    error_message = "API Management must have exactly the gateway identity, user-assigned."
  }
}

run "named_values" {
  command = plan

  assert {
    condition     = length(azurerm_api_management_named_value.this) == 6
    error_message = "There must be exactly the six named values the policies use."
  }

  assert {
    condition = nonsensitive(
      azurerm_api_management_named_value.this["tenant-id"].value == "11111111-1111-1111-1111-111111111111" &&
      azurerm_api_management_named_value.this["gateway-app-client-id"].value == "33333333-3333-3333-3333-333333333333" &&
      azurerm_api_management_named_value.this["gateway-identity-client-id"].value == "22222222-2222-2222-2222-222222222222" &&
      azurerm_api_management_named_value.this["tokens-per-minute"].value == "10000" &&
      azurerm_api_management_named_value.this["tokens-per-day"].value == "50000" &&
      azurerm_api_management_named_value.this["primary-backend-host"].value == "aoai-primary.example.com"
    )
    error_message = "Each named value must come from the bootstrap's state or the variables, and primary-backend-host must be the primary URL's host."
  }

  assert {
    condition     = alltrue([for name, value in azurerm_api_management_named_value.this : value.secret == false && value.name == name && value.display_name == name])
    error_message = "No named value is secret, and each is named and displayed as the policies refer to it."
  }
}

run "named_values_follow_the_budgets" {
  command = plan

  variables {
    tokens_per_minute = 2000
    tokens_per_day    = 9000
  }

  assert {
    condition     = nonsensitive(azurerm_api_management_named_value.this["tokens-per-minute"].value == "2000" && azurerm_api_management_named_value.this["tokens-per-day"].value == "9000")
    error_message = "tokens-per-minute and tokens-per-day must follow the variables."
  }
}

run "api" {
  command = plan

  assert {
    condition     = azurerm_api_management_api_version_set.openai.versioning_scheme == "Segment"
    error_message = "The version set must use the segment scheme."
  }

  assert {
    condition     = azurerm_api_management_api.v1.path == "openai" && azurerm_api_management_api.v1.version == "v1" && azurerm_api_management_api.v1.version_set_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apiVersionSets/azure-openai"
    error_message = "The API must be at path openai, version v1 in the version set."
  }

  assert {
    condition     = azurerm_api_management_api.v1.revision == "1" && azurerm_api_management_api.v1.display_name == "Azure OpenAI (v1)"
    error_message = "The API must be revision 1, displayed as Azure OpenAI (v1)."
  }

  assert {
    condition     = azurerm_api_management_api.v1.subscription_required == false && azurerm_api_management_api.v1_rev2.subscription_required == false
    error_message = "No revision may require a subscription key."
  }

  assert {
    condition     = azurerm_api_management_api.v1.protocols == toset(["https"]) && azurerm_api_management_api.v1_rev2.protocols == toset(["https"])
    error_message = "The API must accept HTTPS only."
  }

  assert {
    condition     = azurerm_api_management_api_operation.chat_completions.method == "POST" && azurerm_api_management_api_operation.chat_completions.url_template == "/chat/completions" && azurerm_api_management_api_operation.chat_completions.api_name == "azure-openai-v1"
    error_message = "The one operation must be POST /chat/completions on the API."
  }

  assert {
    condition     = azapi_resource.policy_v1.type == "Microsoft.ApiManagement/service/apis/policies@2024-05-01" && azapi_resource.policy_v1.name == "policy" && azapi_resource.policy_v1.parent_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/azure-openai-v1;rev=1"
    error_message = "Revision 1's policy must be the policy of revision 1 itself, addressed with ;rev=1, not of whichever revision is current."
  }

  assert {
    condition     = azapi_resource.policy_v1.body.properties.format == "xml" && azapi_resource.policy_v1.body.properties.value == file("policies/api-v1.xml")
    error_message = "Revision 1's policy must be policies/api-v1.xml, unchanged, in the xml format its escaped expressions need."
  }
}

run "revision_2" {
  command = plan

  assert {
    condition     = azurerm_api_management_api.v1_rev2.name == "azure-openai-v1" && azurerm_api_management_api.v1_rev2.revision == "2" && azurerm_api_management_api.v1_rev2.source_api_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/azure-openai-v1;rev=1"
    error_message = "Revision 2 must be revision 2 of the same API, made from revision 1."
  }

  assert {
    condition     = azapi_resource.policy_v1_rev2.type == "Microsoft.ApiManagement/service/apis/policies@2024-05-01" && azapi_resource.policy_v1_rev2.name == "policy" && azapi_resource.policy_v1_rev2.parent_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/azure-openai-v1;rev=2"
    error_message = "Revision 2's policy must be the policy of revision 2, addressed with ;rev=2."
  }

  assert {
    condition     = azapi_resource.policy_v1_rev2.body.properties.format == "xml" && azapi_resource.policy_v1_rev2.body.properties.value == file("policies/api-v1-rev2.xml")
    error_message = "Revision 2's policy must be policies/api-v1-rev2.xml, unchanged, in the xml format."
  }

  assert {
    condition     = length(azurerm_api_management_api_release.revision_2) == 0
    error_message = "Revision 2 must not be made current by default."
  }
}

run "revision_2_released" {
  command = plan

  variables {
    release_revision_2 = true
  }

  assert {
    condition     = length(azurerm_api_management_api_release.revision_2) == 1 && azurerm_api_management_api_release.revision_2[0].api_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/azure-openai-v1;rev=2"
    error_message = "With release_revision_2, one release must make revision 2 current."
  }
}

run "backends" {
  command = plan

  assert {
    condition     = length(azapi_resource.backend) == 2 && alltrue([for backend in azapi_resource.backend : backend.type == "Microsoft.ApiManagement/service/backends@2024-05-01" && backend.parent_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3"])
    error_message = "There must be two backends, on the service, at API version 2024-05-01."
  }

  assert {
    condition     = azapi_resource.backend["aoai-primary"].body.properties.url == "https://aoai-primary.example.com/openai/v1" && azapi_resource.backend["aoai-secondary"].body.properties.url == "https://aoai-secondary.example.com/openai/v1"
    error_message = "The backends' URLs must be the bootstrap's two backend URLs, as they are."
  }

  assert {
    condition     = alltrue([for backend in azapi_resource.backend : backend.body.properties.type == "Single" && backend.body.properties.protocol == "http"])
    error_message = "Both backends must be Single, over http (the REST protocol)."
  }

  assert {
    condition = alltrue([for backend in azapi_resource.backend :
      length(backend.body.properties.circuitBreaker.rules) == 1 &&
      backend.body.properties.circuitBreaker.rules[0].failureCondition.count == 1 &&
      backend.body.properties.circuitBreaker.rules[0].failureCondition.interval == "PT5M" &&
      length(backend.body.properties.circuitBreaker.rules[0].failureCondition.statusCodeRanges) == 1 &&
      backend.body.properties.circuitBreaker.rules[0].failureCondition.statusCodeRanges[0].min == 429 &&
      backend.body.properties.circuitBreaker.rules[0].failureCondition.statusCodeRanges[0].max == 429 &&
      backend.body.properties.circuitBreaker.rules[0].tripDuration == "PT1M" &&
      backend.body.properties.circuitBreaker.rules[0].acceptRetryAfter == true
    ])
    error_message = "Each backend must have one breaker rule: one 429 in 5 minutes trips it for 1 minute, or for the Retry-After."
  }

  assert {
    condition     = azapi_resource.pool.name == "aoai-pool" && azapi_resource.pool.type == "Microsoft.ApiManagement/service/backends@2024-05-01" && azapi_resource.pool.body.properties.type == "Pool"
    error_message = "The pool must be the backend aoai-pool, of type Pool."
  }

  assert {
    condition = (
      length(azapi_resource.pool.body.properties.pool.services) == 2 &&
      azapi_resource.pool.body.properties.pool.services[0].id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/backends/aoai-primary" &&
      azapi_resource.pool.body.properties.pool.services[0].priority == 1 &&
      azapi_resource.pool.body.properties.pool.services[1].id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/backends/aoai-secondary" &&
      azapi_resource.pool.body.properties.pool.services[1].priority == 2
    )
    error_message = "The pool must hold the primary at priority 1 and the secondary at priority 2."
  }
}

run "monitoring" {
  command = plan

  assert {
    condition     = azurerm_api_management_logger.appi.resource_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-bootstrap/providers/Microsoft.Insights/components/appi-releaselens" && azurerm_api_management_logger.appi.application_insights[0].identity_client_id == "22222222-2222-2222-2222-222222222222"
    error_message = "The logger must point at the bootstrap's Application Insights and ingest as the gateway identity."
  }

  assert {
    condition     = azurerm_api_management_api_diagnostic.appi.api_name == "azure-openai-v1" && azurerm_api_management_api_diagnostic.appi.identifier == "applicationinsights" && azurerm_api_management_api_diagnostic.appi.api_management_logger_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/loggers/appi-releaselens"
    error_message = "The API's Application Insights diagnostic must use the logger."
  }

  assert {
    condition     = azurerm_api_management_api_diagnostic.appi.sampling_percentage == 100 && azurerm_api_management_api_diagnostic.appi.log_client_ip == false
    error_message = "The diagnostic must sample every request, and log no client IP."
  }

  assert {
    condition = (
      azurerm_api_management_api_diagnostic.appi.frontend_request[0].body_bytes == 0 &&
      azurerm_api_management_api_diagnostic.appi.frontend_response[0].body_bytes == 0 &&
      azurerm_api_management_api_diagnostic.appi.backend_request[0].body_bytes == 0 &&
      azurerm_api_management_api_diagnostic.appi.backend_response[0].body_bytes == 0
    )
    error_message = "The diagnostic must log no request or response body, in front or behind."
  }

  assert {
    condition     = azapi_update_resource.diagnostic_metrics.type == "Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01" && azapi_update_resource.diagnostic_metrics.resource_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/azure-openai-v1/diagnostics/applicationinsights" && azapi_update_resource.diagnostic_metrics.body.properties.metrics == true
    error_message = "The metrics update must set metrics = true on the API's diagnostic."
  }
}

# Revision 2 inherits the API's diagnostic when it is created, but not its metrics switch (seen
# live on 2026-10-09: its calls were logged and emitted no token metric). So revision 2's own
# diagnostic gets metrics = true, addressed by revision 2's own ID.
run "revision_2_metrics" {
  command = plan

  assert {
    condition     = azapi_update_resource.diagnostic_metrics_rev2.type == "Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01"
    error_message = "Revision 2's metrics update must target an API diagnostic."
  }

  assert {
    condition     = azapi_update_resource.diagnostic_metrics_rev2.resource_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/apis/azure-openai-v1;rev=2/diagnostics/applicationinsights"
    error_message = "Revision 2's metrics update must target revision 2's own Application Insights diagnostic."
  }

  assert {
    condition     = azapi_update_resource.diagnostic_metrics_rev2.body.properties.metrics == true
    error_message = "Revision 2's diagnostic must have metrics = true."
  }
}

run "outputs" {
  command = plan

  assert {
    condition     = output.gateway_base_url == "https://apim-releaselens-a1b2c3.azure-api.net/openai/v1/"
    error_message = "gateway_base_url must be https://<gateway host>/openai/v1/."
  }

  assert {
    condition     = output.gateway_scope == "api://33333333-3333-3333-3333-333333333333/.default"
    error_message = "gateway_scope must be api://<gateway app client id>/.default."
  }

  # The functions stack publishes the search tool on this service, through this logger.
  assert {
    condition     = output.api_management_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3"
    error_message = "api_management_id must be the service's resource ID."
  }

  assert {
    condition     = output.gateway_url == "https://apim-releaselens-a1b2c3.azure-api.net"
    error_message = "gateway_url must be the service's gateway URL, https://<gateway host>."
  }

  assert {
    condition     = output.app_insights_logger_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-releaselens-gateway/providers/Microsoft.ApiManagement/service/apim-releaselens-a1b2c3/loggers/appi-releaselens"
    error_message = "app_insights_logger_id must be the Application Insights logger's resource ID."
  }
}
