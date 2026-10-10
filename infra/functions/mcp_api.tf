# The search tool on the gateway (spec §3.3, §5.2): an MCP API on the gateway stack's API
# Management service, passing Streamable HTTP through to the tool app. Everything here is azapi:
# azurerm has no resource for the mcp API type, which needs 2025-09-01-preview (research notes
# §4). It is created under the gateway's service but lives in this stack's state, so it goes
# with this stack's destroy; destroying the gateway first would delete it with the service.

# The values the policy refers to as {{name}}. None is secret: they are identifiers, kept out of
# the repository by coming from the bootstrap's state. Prefixed tool-, so they never collide with
# the gateway stack's own named values.
locals {
  named_values = {
    "tool-tenant-id"                  = local.bootstrap.tenant_id
    "tool-gateway-app-client-id"      = local.bootstrap.gateway_app_client_id
    "tool-app-audience"               = local.bootstrap.search_tool_app_identifier_uri
    "tool-gateway-identity-client-id" = local.bootstrap.gateway_identity_client_id
  }

  # Clients call https://<gateway host>/releaselens-search/mcp.
  mcp_api_path = "releaselens-search"
  tool_host    = azapi_resource.app["tool"].output.properties.defaultHostName
}

resource "azapi_resource" "named_value" {
  for_each = local.named_values

  type      = "Microsoft.ApiManagement/service/namedValues@2024-05-01"
  name      = each.key
  parent_id = local.gateway.api_management_id

  body = {
    properties = {
      displayName = each.key
      value       = each.value
      secret      = false
    }
  }
}

# The path split is the research notes' shape (§4), unverified live: the service URL ends at
# /runtime/webhooks and the one endpoint's template is /mcp, so a call reaches the tool app's
# /runtime/webhooks/mcp. No subscription key: the Entra token the policy validates is the only
# credential, and no product or subscription exists.
resource "azapi_resource" "mcp_api" {
  type      = "Microsoft.ApiManagement/service/apis@2025-09-01-preview"
  name      = "releaselens-search"
  parent_id = local.gateway.api_management_id

  body = {
    properties = {
      type                 = "mcp"
      displayName          = "ReleaseLens search tool"
      description          = "The search_corpus tool of the ReleaseLens search Function app, over Streamable HTTP."
      path                 = local.mcp_api_path
      protocols            = ["https"]
      subscriptionRequired = false
      serviceUrl           = "https://${local.tool_host}/runtime/webhooks"
      mcpProperties = {
        transportType = "streamable"
        endpoints = [
          {
            name        = "message"
            uriTemplate = "/mcp"
          },
        ]
      }
    }
  }
}

# The policy is the file, so the static checks in tests/infra parse what is deployed.
# format = "xml": the file is well-formed XML with its expressions escaped (&gt;=).
resource "azapi_resource" "mcp_policy" {
  type      = "Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview"
  name      = "policy"
  parent_id = azapi_resource.mcp_api.id

  body = {
    properties = {
      format = "xml"
      value  = file("${path.module}/policies/mcp-api.xml")
    }
  }

  # API Management checks the policy's {{name}} references when it is saved.
  depends_on = [azapi_resource.named_value]
}

# emit-metric publishes only while the API's diagnostic has metrics on, so the MCP API gets its
# own Application Insights diagnostic, through the gateway stack's logger (read from its state).
# Every request is recorded, with no payload in front or behind: reading a body would buffer
# Streamable HTTP, and no query or result leaves the gateway in a log (spec §5.2, §8).
#
# An azapi resource, not azurerm's diagnostic plus an azapi update as the gateway stack has:
# azapi writes the whole body, metrics included, so no later PUT can switch metrics off, and it
# addresses the MCP API at the API version that knows its type.
resource "azapi_resource" "mcp_diagnostic" {
  type      = "Microsoft.ApiManagement/service/apis/diagnostics@2025-09-01-preview"
  name      = "applicationinsights"
  parent_id = azapi_resource.mcp_api.id

  body = {
    properties = {
      loggerId  = local.gateway.app_insights_logger_id
      alwaysLog = "allErrors"
      sampling = {
        samplingType = "fixed"
        percentage   = 100
      }
      logClientIp             = false
      verbosity               = "information"
      httpCorrelationProtocol = "W3C"
      frontend = {
        request = {
          body = {
            bytes = 0
          }
        }
        response = {
          body = {
            bytes = 0
          }
        }
      }
      backend = {
        request = {
          body = {
            bytes = 0
          }
        }
        response = {
          body = {
            bytes = 0
          }
        }
      }
      largeLanguageModel = {
        logs = "disabled"
      }
      metrics = true
    }
  }
}
