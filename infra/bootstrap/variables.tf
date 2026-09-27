variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
}

variable "location" {
  type        = string
  description = "Azure region."
  default     = "australiaeast"
}

variable "budget_amount_usd" {
  type        = number
  description = "Monthly budget. Alerts fire when actual spend reaches 50, 80 and 100 percent of it, and when forecast spend reaches 100 percent. Azure cannot hard-stop spend on a pay-as-you-go subscription."
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
