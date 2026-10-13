locals {
  targets = { main = azapi_resource.main.id, staging = azapi_resource.staging.id }
  auth_properties = {
    platform = { enabled = true }
    globalValidation = {
      requireAuthentication       = true
      unauthenticatedClientAction = "RedirectToLoginPage"
      redirectToProvider          = "azureActiveDirectory"
      excludedPaths               = ["/signed-out", "/site.css", "/site.js"]
    }
    httpSettings = { requireHttps = true }
    login = {
      tokenStore                    = { enabled = true }
      preserveUrlFragmentsForLogins = false
    }
    identityProviders = {
      # Azure otherwise returns enabled defaults for omitted providers.
      apple                        = { enabled = false }
      azureStaticWebApps           = { enabled = false }
      facebook                     = { enabled = false }
      gitHub                       = { enabled = false }
      google                       = { enabled = false }
      legacyMicrosoftAccount       = { enabled = false }
      twitter                      = { enabled = false }
      customOpenIdConnectProviders = {}
      azureActiveDirectory = {
        enabled = true
        registration = {
          openIdIssuer            = "https://login.microsoftonline.com/${local.bootstrap.tenant_id}/v2.0"
          clientId                = local.bootstrap.webapp_app_client_id
          clientSecretSettingName = "OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID"
        }
        login = {
          loginParameters = ["scope=openid profile offline_access api://${local.bootstrap.gateway_app_client_id}/access_as_user"]
        }
        validation = {
          allowedAudiences = [local.bootstrap.webapp_app_client_id, local.bootstrap.webapp_app_identifier_uri]
          defaultAuthorizationPolicy = {
            # An empty groups array still invokes Easy Auth's Graph lookup for
            # app-only smoke tokens. Null disables it; only these identities pass.
            allowedPrincipals = { identities = [local.bootstrap.webapp_owner_object_id, local.bootstrap.deploy_identity_principal_id], groups = null }
          }
        }
      }
    }
  }
  basic_publishing = { for pair in setproduct(["main", "staging"], ["scm", "ftp"]) : "${pair[0]}-${pair[1]}" => { slot = pair[0], kind = pair[1] } }
}
resource "azapi_update_resource" "auth" {
  for_each    = local.targets
  type        = each.key == "main" ? "Microsoft.Web/sites/config@2024-11-01" : "Microsoft.Web/sites/slots/config@2024-11-01"
  resource_id = "${each.value}/config/authsettingsV2"
  body        = { properties = local.auth_properties }
}
resource "azapi_update_resource" "basic_publishing" {
  for_each    = local.basic_publishing
  type        = each.value.slot == "main" ? "Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01" : "Microsoft.Web/sites/slots/basicPublishingCredentialsPolicies@2024-11-01"
  resource_id = "${local.targets[each.value.slot]}/basicPublishingCredentialsPolicies/${each.value.kind}"
  body        = { properties = { allow = false } }
}
resource "azapi_update_resource" "sticky" {
  type        = "Microsoft.Web/sites/config@2024-11-01"
  resource_id = "${azapi_resource.main.id}/config/slotConfigNames"
  body = {
    properties = {
      appSettingNames         = ["Web__GatewayBaseUrl", "Web__Model", "Web__TenantId", "Web__GatewayAudience", "Web__OwnerObjectId", "Web__DeployObjectId", "Web__Slot", "OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID", "ASPNETCORE_ENVIRONMENT"]
      connectionStringNames   = []
      azureStorageConfigNames = []
    }
  }
}
