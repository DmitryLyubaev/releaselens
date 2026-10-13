variables {
  subscription_id         = "00000000-0000-0000-0000-000000000000"
  tfstate_storage_account = "mockstate"
}

run "entra_only_without_platform_default_providers" {
  command = plan
  assert {
    condition = alltrue([for auth in values(azapi_update_resource.auth) :
      try(auth.body.properties.identityProviders.azureActiveDirectory.enabled &&
        alltrue([for provider in ["apple", "azureStaticWebApps", "facebook", "gitHub", "google", "legacyMicrosoftAccount", "twitter"] :
        auth.body.properties.identityProviders[provider].enabled == false]) &&
      length(auth.body.properties.identityProviders.customOpenIdConnectProviders) == 0, false)
    ])
    error_message = "Both slots must explicitly disable non-Entra providers so platform defaults cannot refuse the release gate."
  }
}
mock_provider "azurerm" {
  override_during = plan
  mock_resource "azurerm_resource_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock-webapp" }
  }
  mock_resource "azurerm_service_plan" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock-webapp/providers/Microsoft.Web/serverfarms/mock" }
  }
}
mock_provider "azapi" { override_during = plan }
override_resource {
  target          = azapi_resource.main
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock-webapp/providers/Microsoft.Web/sites/mock-webapp"
  }
}
override_resource {
  target          = azapi_resource.staging
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock-webapp/providers/Microsoft.Web/sites/mock-webapp/slots/staging"
  }
}
override_data {
  target = data.terraform_remote_state.bootstrap
  values = {
    outputs = {
      webapp_name                  = "mock-webapp"
      webapp_identity_id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock-bootstrap/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mock-webapp"
      webapp_identity_client_id    = "00000000-0000-0000-0000-000000000001"
      webapp_identity_principal_id = "00000000-0000-0000-0000-000000000002"
      webapp_app_client_id         = "00000000-0000-0000-0000-000000000003"
      webapp_app_identifier_uri    = "api://00000000-0000-0000-0000-000000000003"
      webapp_owner_object_id       = "00000000-0000-0000-0000-000000000004"
      deploy_identity_principal_id = "00000000-0000-0000-0000-000000000005"
      gateway_app_client_id        = "00000000-0000-0000-0000-000000000006"
      tenant_id                    = "00000000-0000-0000-0000-000000000007"
      azure_openai_deployment      = "mock-model"
    }
  }
}
override_data {
  target = data.terraform_remote_state.gateway
  values = {
    outputs = { gateway_base_url = "https://gateway.example.test/azure-openai/v1/" }
  }
}

run "one_linux_s1_and_shared_identity" {
  command = plan
  assert {
    condition     = azurerm_service_plan.webapp.os_type == "Linux" && azurerm_service_plan.webapp.sku_name == "S1" && azurerm_service_plan.webapp.worker_count == 1 && azurerm_resource_group.webapp.location == "australiaeast"
    error_message = "The session requires one Linux S1 instance in Australia East."
  }
  assert {
    condition     = azapi_resource.main.identity[0].type == "UserAssigned" && azapi_resource.main.identity[0].identity_ids == azapi_resource.staging.identity[0].identity_ids && length(azapi_resource.main.identity[0].identity_ids) == 1
    error_message = "Main and staging must attach the same dedicated user-assigned identity."
  }
  assert {
    condition = alltrue([for site in [azapi_resource.main, azapi_resource.staging] :
      site.body.properties.httpsOnly && site.body.properties.siteConfig.alwaysOn &&
      site.body.properties.siteConfig.linuxFxVersion == "DOTNETCORE|10.0" &&
      site.body.properties.siteConfig.appCommandLine == "dotnet ReleaseLens.Web.dll" &&
      site.body.properties.siteConfig.ftpsState == "Disabled" &&
      site.body.properties.siteConfig.minTlsVersion == "1.3" && site.body.properties.siteConfig.scmMinTlsVersion == "1.3"
    ])
    error_message = "Both slots require the same protected .NET 10 runtime and startup."
  }
  assert {
    condition     = azurerm_role_assignment.deploy_webapp.role_definition_name == "Website Contributor" && azurerm_role_assignment.deploy_webapp.scope == azapi_resource.main.id && azurerm_role_assignment.deploy_webapp.principal_id == "00000000-0000-0000-0000-000000000005"
    error_message = "Deployment permission must be scoped to this web app only."
  }
}

run "authentication_and_sticky_environment" {
  command = plan
  assert {
    condition = alltrue([for auth in values(azapi_update_resource.auth) :
      auth.body.properties.platform.enabled && auth.body.properties.globalValidation.requireAuthentication &&
      auth.body.properties.globalValidation.unauthenticatedClientAction == "RedirectToLoginPage" &&
      auth.body.properties.globalValidation.excludedPaths == ["/signed-out", "/site.css", "/site.js"] &&
      auth.body.properties.httpSettings.requireHttps && auth.body.properties.login.tokenStore.enabled &&
      auth.body.properties.identityProviders.azureActiveDirectory.registration.clientSecretSettingName == "OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID" &&
      auth.body.properties.identityProviders.azureActiveDirectory.registration.clientId == "00000000-0000-0000-0000-000000000003" &&
      auth.body.properties.identityProviders.azureActiveDirectory.registration.openIdIssuer == "https://login.microsoftonline.com/00000000-0000-0000-0000-000000000007/v2.0" &&
      auth.body.properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedPrincipals.identities == ["00000000-0000-0000-0000-000000000004", "00000000-0000-0000-0000-000000000005"] &&
      auth.body.properties.identityProviders.azureActiveDirectory.login.loginParameters == ["scope=openid profile offline_access api://00000000-0000-0000-0000-000000000006/access_as_user"]
    ])
    error_message = "Authentication must allow only owner/deploy, with keyless delegated token storage on both slots."
  }
  assert {
    condition     = length(azapi_update_resource.basic_publishing) == 4 && alltrue([for policy in values(azapi_update_resource.basic_publishing) : !policy.body.properties.allow])
    error_message = "SCM and FTP basic credentials must be disabled on main and staging."
  }
  assert {
    condition     = alltrue([for auth in values(azapi_update_resource.auth) : auth.body.properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedPrincipals.groups == null])
    error_message = "Group lookup must be disabled without changing the two-principal identity allowlist."
  }
  assert {
    condition     = toset(azapi_update_resource.sticky.body.properties.appSettingNames) == toset(["Web__GatewayBaseUrl", "Web__Model", "Web__TenantId", "Web__GatewayAudience", "Web__OwnerObjectId", "Web__DeployObjectId", "Web__Slot", "OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID", "ASPNETCORE_ENVIRONMENT"])
    error_message = "Environment and assertion identity stay with each slot; package selection moves with code."
  }
  assert {
    condition = alltrue([for pair in { main = azapi_resource.main, staging = azapi_resource.staging } :
      { for setting in pair.body.properties.siteConfig.appSettings : setting.name => setting.value }.WEBSITE_RUN_FROM_PACKAGE == "1" &&
      { for setting in pair.body.properties.siteConfig.appSettings : setting.name => setting.value }.SCM_DO_BUILD_DURING_DEPLOYMENT == "false" &&
      { for setting in pair.body.properties.siteConfig.appSettings : setting.name => setting.value }.ENABLE_ORYX_BUILD == "false" &&
      { for setting in pair.body.properties.siteConfig.appSettings : setting.name => setting.value }.ASPNETCORE_ENVIRONMENT == "Production" &&
      { for setting in pair.body.properties.siteConfig.appSettings : setting.name => setting.value }.OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID == "00000000-0000-0000-0000-000000000001"
    ])
    error_message = "Immutable compiled packages must use the assertion identity without server-side builds."
  }
  assert {
    condition     = { for setting in azapi_resource.main.body.properties.siteConfig.appSettings : setting.name => setting.value }.Web__Slot == "main" && { for setting in azapi_resource.staging.body.properties.siteConfig.appSettings : setting.name => setting.value }.Web__Slot == "staging"
    error_message = "Slot labels must remain distinct from package version."
  }
}
