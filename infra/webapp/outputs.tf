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
