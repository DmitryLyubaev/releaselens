variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
}

variable "resource_group_name" {
  type        = string
  description = "The resource group everything here goes in. Bootstrap creates it and gives the deploy identity Contributor on it; this stack does not own it."
  default     = "rg-releaselens"
}

variable "location" {
  type        = string
  description = "Azure region."
  default     = "australiaeast"
}

# The four handoff values. Bootstrap outputs them, the owner copies them into the GitHub
# environment's variables, and the workflows pass them in as TF_VAR_*.

variable "app_identity_id" {
  type        = string
  description = "Resource ID of the app identity, the only identity the Container App runs as. From APP_IDENTITY_ID."
}

variable "app_identity_client_id" {
  type        = string
  description = "Client ID of the app identity, set as the container's AZURE_CLIENT_ID. From APP_IDENTITY_CLIENT_ID."
}

variable "azure_openai_base_url" {
  type        = string
  description = "The Azure OpenAI account's v1 base URL. From AZURE_OPENAI_BASE_URL."
}

variable "azure_openai_deployment" {
  type        = string
  description = "Name of the gpt-4.1-mini deployment. From AZURE_OPENAI_DEPLOYMENT."
}

# The pricing identity: the app looks its rate up by these three, and Azure never sees them.
# They must describe the deployment bootstrap created.

variable "azure_openai_model" {
  type        = string
  description = "Model of the deployment, for pricing only."
  default     = "gpt-4.1-mini"
}

variable "azure_openai_model_version" {
  type        = string
  description = "Model version of the deployment, for pricing only."
  default     = "2025-04-14"
}

variable "azure_openai_deployment_type" {
  type        = string
  description = "Deployment type of the deployment, for pricing only."
  default     = "GlobalStandard"
}

variable "smoke_runner_ip" {
  type        = string
  description = "Public IP of the GitHub runner the smoke test runs on. While it is empty, the runner has no firewall rule of its own."
  default     = ""
}

variable "postgres_admin_username" {
  type    = string
  default = "releaselens"
}

variable "image" {
  type        = string
  description = "The API image, pinned by digest. The default is an all-zero digest placeholder, so that destroy, which deploys no image, needs none. No image has that digest, so it is not for apply."
  default     = "ghcr.io/dmitrylyubaev/releaselens-api@sha256:0000000000000000000000000000000000000000000000000000000000000000"

  validation {
    condition     = can(regex("^ghcr\\.io/dmitrylyubaev/releaselens-api@sha256:[0-9a-f]{64}$", var.image))
    error_message = "image must be ghcr.io/dmitrylyubaev/releaselens-api pinned by digest, as @sha256: followed by 64 lowercase hex characters. A tag such as :latest is rejected."
  }
}
