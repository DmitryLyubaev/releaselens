# The backends are azapi because azurerm has no pool type (PR #31144 is open), and its backend
# resource has no circuit breaker. The policy routes to the pool, never to a single backend.

locals {
  # The bootstrap's URLs as they are: https://<account>.openai.azure.com/openai/v1, no trailing
  # slash. API Management appends the operation's path, /chat/completions.
  backend_urls = {
    "aoai-primary"   = local.bootstrap.primary_openai_backend_url
    "aoai-secondary" = local.bootstrap.failover_openai_backend_url
  }
}

# One breaker rule each, the official lab's values (spec §3.2): one 429 in five minutes trips the
# backend for a minute, or for as long as the model's Retry-After asks. A tripped primary is how
# the pool's second try reaches the secondary.
resource "azapi_resource" "backend" {
  for_each = local.backend_urls

  type      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name      = each.key
  parent_id = azurerm_api_management.gateway.id

  body = {
    properties = {
      type     = "Single"
      protocol = "http"
      url      = each.value
      circuitBreaker = {
        rules = [
          {
            name = "trip-on-429"
            failureCondition = {
              count    = 1
              interval = "PT5M"
              statusCodeRanges = [
                {
                  min = 429
                  max = 429
                },
              ]
            }
            tripDuration     = "PT1M"
            acceptRetryAfter = true
          },
        ]
      }
    }
  }
}

# Priority, not weight: everything goes to the primary while its breaker is closed.
resource "azapi_resource" "pool" {
  type      = "Microsoft.ApiManagement/service/backends@2024-05-01"
  name      = "aoai-pool"
  parent_id = azurerm_api_management.gateway.id

  body = {
    properties = {
      type = "Pool"
      pool = {
        services = [
          {
            id       = azapi_resource.backend["aoai-primary"].id
            priority = 1
          },
          {
            id       = azapi_resource.backend["aoai-secondary"].id
            priority = 2
          },
        ]
      }
    }
  }
}
