output "webapp_resource_id" {
  value     = azapi_resource.main.id
  sensitive = true
}
output "webapp_main_url" {
  value     = "https://${local.bootstrap.webapp_name}.azurewebsites.net"
  sensitive = true
}
output "webapp_staging_url" {
  value     = "https://${local.bootstrap.webapp_name}-staging.azurewebsites.net"
  sensitive = true
}
output "webapp_frontend_audience" {
  value     = local.bootstrap.webapp_app_identifier_uri
  sensitive = true
}

output "webapp_intended_configuration" {
  description = "Private expected values for release-time configuration comparisons."
  value = merge({ for slot in ["main", "staging"] : slot => {
    identity    = { type = "UserAssigned", identity_ids = [local.bootstrap.webapp_identity_id] }
    siteconfig  = { for name, value in local.site_configs[slot] : name => value if name != "appSettings" }
    appsettings = merge(local.common_settings, { Web__Slot = slot })
    auth        = local.auth_properties
    publishing  = { scm = false, ftp = false }
    } }, {
    sticky = {
      appSettingNames         = ["Web__GatewayBaseUrl", "Web__Model", "Web__TenantId", "Web__GatewayAudience", "Web__OwnerObjectId", "Web__DeployObjectId", "Web__Slot", "OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID", "ASPNETCORE_ENVIRONMENT"]
      connectionStringNames   = []
      azureStorageConfigNames = []
    }
  })
  sensitive = true
}
