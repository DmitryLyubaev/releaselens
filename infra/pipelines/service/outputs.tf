# Sensitive so the apply log never shows the host; `terraform output -json search_host` still returns it.
output "search_host" {
  value     = "${azurerm_search_service.search.name}.search.windows.net"
  sensitive = true
}
