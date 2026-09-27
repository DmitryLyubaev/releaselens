resource "azurerm_container_app_environment" "this" {
  name                = "cae-releaselens"
  location            = var.location
  resource_group_name = var.resource_group_name
}

resource "azurerm_container_app" "api" {
  name                         = "ca-releaselens-api"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"

  # The app identity is the only one: it holds Cognitive Services OpenAI User on the account,
  # and the container authenticates to Azure OpenAI as it, with no key.
  identity {
    type         = "UserAssigned"
    identity_ids = [var.app_identity_id]
  }

  secret {
    name  = "database-connection"
    value = local.database_connection_string
  }

  template {
    # Scale to zero. An idle deployment must cost nothing but the database.
    min_replicas = 0
    max_replicas = 2

    container {
      name   = "api"
      image  = var.image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name        = "RELEASELENS_DB"
        secret_name = "database-connection"
      }

      env {
        name  = "ASPNETCORE_ENVIRONMENT"
        value = "Production"
      }

      env {
        name  = "Chat__Providers__0"
        value = "azure-openai"
      }

      env {
        name  = "AzureOpenAi__BaseUrl"
        value = var.azure_openai_base_url
      }

      env {
        name  = "AzureOpenAi__Deployment"
        value = var.azure_openai_deployment
      }

      env {
        name  = "AzureOpenAi__Credential"
        value = "ManagedIdentity"
      }

      env {
        name  = "AzureOpenAi__Model"
        value = var.azure_openai_model
      }

      env {
        name  = "AzureOpenAi__ModelVersion"
        value = var.azure_openai_model_version
      }

      env {
        name  = "AzureOpenAi__DeploymentType"
        value = var.azure_openai_deployment_type
      }

      # The app's managed-identity credential needs the client ID: without one it would ask for
      # a system-assigned identity, which the container does not have.
      env {
        name  = "AZURE_CLIENT_ID"
        value = var.app_identity_client_id
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
      concurrent_requests = "10"
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
