# Only the endpoint: with key authentication off, the service's keys are never needed, and none
# is output.
output "endpoint" {
  description = "The search service's endpoint. build-index and run-arms take it as --endpoint."
  value       = "https://${azurerm_search_service.search.name}.search.windows.net"
}
