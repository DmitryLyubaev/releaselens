# The harness's and the runbook's inputs (spec §7). Identifiers and URLs, not credentials: no key
# exists to output, and the one sensitive setting is never output.

output "tool_mcp_url" {
  description = "The gateway's MCP endpoint for the search tool, https://<gateway host>/releaselens-search/mcp. The harness and Claude Code call it."
  value       = "${local.gateway.gateway_url}/${local.mcp_api_path}/mcp"
}

output "tool_app_url" {
  description = "The tool app's own Streamable HTTP endpoint, https://<tool app host>/runtime/webhooks/mcp. T3 calls it directly to show App Service authentication refusing a caller that is not the gateway."
  value       = "https://${local.tool_host}/runtime/webhooks/mcp"
}

output "ingest_app_name" {
  description = "Name of the ingest app, for deploying its package."
  value       = azapi_resource.app["ingest"].name
}

output "tool_app_name" {
  description = "Name of the tool app, for deploying its package."
  value       = azapi_resource.app["tool"].name
}
