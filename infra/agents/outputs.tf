output "owned" {
  value = {
    session      = var.session_name
    group_name   = azurerm_resource_group.agent.name
    group_id     = azurerm_resource_group.agent.id
    worker_job   = azurerm_container_app_job.worker.id
    setup_job    = azurerm_container_app_job.placeholder.id
    vault_id     = azurerm_key_vault.agent.id
    secret_uri   = "${azurerm_key_vault.agent.vault_uri}secrets/azp-token"
    identity_id  = azurerm_user_assigned_identity.agent.id
    source       = var.source_sha
    image_digest = var.image_digest
  }
  sensitive = true
}
