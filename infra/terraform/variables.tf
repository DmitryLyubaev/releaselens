variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
}

variable "location" {
  type        = string
  description = "Azure region."
  default     = "australiaeast"
}

variable "prefix" {
  type        = string
  description = "Name prefix for every resource."
  default     = "releaselens"
}

variable "budget_amount_usd" {
  type        = number
  description = "Monthly budget. Alerts fire at 50, 80 and 100 percent. Azure cannot hard-stop spend on a pay-as-you-go subscription."
  default     = 50
}

variable "budget_alert_email" {
  type        = string
  description = "Address that receives budget alerts."
}

variable "budget_start_date" {
  type        = string
  description = "First day of the budget period, in YYYY-MM-01T00:00:00Z form. Must be the first of a month."
}

variable "postgres_admin_username" {
  type    = string
  default = "releaselens"
}

variable "anthropic_api_key" {
  type        = string
  description = "Stored in Key Vault, never in state you commit."
  sensitive   = true
}

variable "openai_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "github_token" {
  type      = string
  sensitive = true
  default   = ""
}
