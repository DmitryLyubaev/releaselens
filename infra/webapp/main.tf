# Paid resources belong to one approved session. Bootstrap retains the identity.
resource "azurerm_resource_group" "webapp" {
  name     = "rg-releaselens-webapp"
  location = var.location
  tags     = { project = "releaselens", stack = "webapp", managedby = "terraform" }
}
resource "azurerm_service_plan" "webapp" {
  name                = "asp-releaselens-webapp"
  resource_group_name = azurerm_resource_group.webapp.name
  location            = azurerm_resource_group.webapp.location
  os_type             = "Linux"
  sku_name            = "S1"
  worker_count        = 1
}
locals {
  common_settings = {
    Web__GatewayBaseUrl                    = local.gateway.gateway_base_url
    Web__Model                             = local.bootstrap.azure_openai_deployment
    Web__TenantId                          = local.bootstrap.tenant_id
    Web__GatewayAudience                   = local.bootstrap.gateway_app_client_id
    Web__OwnerObjectId                     = local.bootstrap.webapp_owner_object_id
    Web__DeployObjectId                    = local.bootstrap.deploy_identity_principal_id
    OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID = local.bootstrap.webapp_identity_client_id
    ASPNETCORE_ENVIRONMENT                 = "Production"
    WEBSITE_RUN_FROM_PACKAGE               = "1"
    SCM_DO_BUILD_DURING_DEPLOYMENT         = "false"
    ENABLE_ORYX_BUILD                      = "false"
  }
  site_configs = { for slot in ["main", "staging"] : slot => {
    linuxFxVersion   = "DOTNETCORE|10.0"
    appCommandLine   = "dotnet ReleaseLens.Web.dll"
    alwaysOn         = true
    minTlsVersion    = "1.3"
    scmMinTlsVersion = "1.3"
    ftpsState        = "Disabled"
    http20Enabled    = true
    appSettings      = [for name, value in merge(local.common_settings, { Web__Slot = slot }) : { name = name, value = value }]
  } }
}
resource "azapi_resource" "main" {
  type      = "Microsoft.Web/sites@2024-11-01"
  name      = local.bootstrap.webapp_name
  parent_id = azurerm_resource_group.webapp.id
  location  = azurerm_resource_group.webapp.location
  identity {
    type         = "UserAssigned"
    identity_ids = [local.bootstrap.webapp_identity_id]
  }
  body = {
    kind = "app,linux"
    properties = {
      serverFarmId = azurerm_service_plan.webapp.id
      reserved     = true
      httpsOnly    = true
      siteConfig   = local.site_configs.main
    }
  }
}
resource "azapi_resource" "staging" {
  type      = "Microsoft.Web/sites/slots@2024-11-01"
  name      = "staging"
  parent_id = azapi_resource.main.id
  location  = azurerm_resource_group.webapp.location
  identity {
    type         = "UserAssigned"
    identity_ids = [local.bootstrap.webapp_identity_id]
  }
  body = {
    kind = "app,linux"
    properties = {
      serverFarmId = azurerm_service_plan.webapp.id
      reserved     = true
      httpsOnly    = true
      siteConfig   = local.site_configs.staging
    }
  }
}
resource "azurerm_role_assignment" "deploy_webapp" {
  scope                = azapi_resource.main.id
  role_definition_name = "Website Contributor"
  principal_id         = local.bootstrap.deploy_identity_principal_id
}
