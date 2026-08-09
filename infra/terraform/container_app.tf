resource "azurerm_container_app_environment" "this" {
  name                       = "${var.prefix}-env"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
}

resource "azurerm_container_app" "api" {
  name                         = "${var.prefix}-api"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"

  identity {
    type = "SystemAssigned"
  }

  secret {
    name  = "database-connection"
    value = azurerm_key_vault_secret.database_connection.value
  }

  secret {
    name  = "anthropic-api-key"
    value = var.anthropic_api_key
  }

  template {
    # Scale to zero. An idle deployment must cost nothing but the database.
    min_replicas = 0
    max_replicas = 2

    container {
      name   = "api"
      image  = "ghcr.io/dmitrylyubaev/releaselens-api:latest"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name        = "RELEASELENS_DB"
        secret_name = "database-connection"
      }

      env {
        name        = "ANTHROPIC_API_KEY"
        secret_name = "anthropic-api-key"
      }

      env {
        name  = "ASPNETCORE_ENVIRONMENT"
        value = "Production"
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/health"
        port      = 8080
      }

      readiness_probe {
        transport = "HTTP"
        path      = "/health"
        port      = 8080
      }
    }

    http_scale_rule {
      name                = "http-concurrency"
      concurrent_requests = 10
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8080
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}
