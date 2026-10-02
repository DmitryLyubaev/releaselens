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

variable "github_oidc_subject" {
  type        = string
  description = "Subject of the deploy identity's federated credential: exactly the sub the OIDC probe printed (R7). It must start with this repository's immutable prefix, carry the claim environment:azure followed by a colon or the end of the value, and contain no ref:. While it is null, no credential exists."
  default     = null
  nullable    = true

  validation {
    # The string functions error on null. try() makes them false instead, so a null subject
    # passes on the left of || whether or not Terraform short-circuits it. The environment
    # claim is matched up to its delimiter, so environment:azure-staging does not pass.
    # Claims a customised template adds after it still pass, unless one contains ref: (as
    # job_workflow_ref does).
    condition = var.github_oidc_subject == null || try(
      startswith(var.github_oidc_subject, "repo:DmitryLyubaev@57339946/releaselens@1331560542:")
      && can(regex(":environment:azure(:|$)", var.github_oidc_subject))
      && !strcontains(var.github_oidc_subject, "ref:"),
      false
    )
    error_message = "github_oidc_subject must be exactly the sub the OIDC probe printed (R7). It must start with repo:DmitryLyubaev@57339946/releaselens@1331560542:, carry the claim environment:azure followed by a colon or the end of the value (so no other environment, such as azure-staging, passes), and contain no ref: anywhere, because a branch-type credential must never exist."
  }
}

variable "azure_openai_deployment_name" {
  type        = string
  description = "Name of the Azure OpenAI deployment of gpt-4.1-mini."
  default     = "releaselens-chat"
}

variable "azure_openai_capacity" {
  type        = number
  description = "Capacity of the Global Standard deployment, in thousands of tokens per minute: 300 is 300,000 TPM. It caps how fast spend can grow, not how much. The default is revised from the dry run's measured token counts (spec §4.8)."
  default     = 300
}
