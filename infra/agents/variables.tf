variable "subscription_id" { type = string }
variable "tenant_id" { type = string }
variable "owner_object_id" { type = string }
variable "owner_account" {
  type        = string
  description = "Private exact personal signed-in account name, checked without printing it."
  validation {
    condition     = length(var.owner_account) > 3 && alltrue([for domain in var.forbidden_account_domains : !endswith(lower(var.owner_account), "@${lower(domain)}")])
    error_message = "Only the personal owner account may seed this vault."
  }
}
variable "forbidden_account_domains" {
  type        = list(string)
  description = "Privately supplied employer/account domain denylist, never a public literal."
  validation {
    condition     = length(var.forbidden_account_domains) > 0 && alltrue([for domain in var.forbidden_account_domains : can(regex("^[A-Za-z0-9.-]+$", domain))])
    error_message = "Provide the private account-boundary domains."
  }
}
variable "location" {
  type    = string
  default = "australiaeast"
}
variable "session_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{3,15}$", var.session_name))
    error_message = "Use a fresh 4-16 character lowercase session slug."
  }
}
variable "organization_url" {
  type = string
  validation {
    condition     = can(regex("^https://dev[.]azure[.]com/[A-Za-z0-9_-]+/?$", var.organization_url))
    error_message = "Use the private personal organisation URL."
  }
}
variable "pool_name" {
  type = string
  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,50}$", var.pool_name))
    error_message = "Use one dedicated pool name."
  }
}
variable "image_digest" {
  type = string
  validation {
    condition     = can(regex("^ghcr[.]io/[a-z0-9_-]+/releaselens-agent@sha256:[0-9a-f]{64}$", var.image_digest))
    error_message = "A verified anonymous-pull GHCR agent digest is required."
  }
}
variable "pat_file" {
  type        = string
  description = "Private WSL absolute path only, never the PAT value."
  validation {
    condition     = startswith(var.pat_file, "/")
    error_message = "Apply uses the approved WSL owner runtime and an absolute private input path."
  }
}
variable "session_dir" {
  type = string
  validation {
    condition     = startswith(var.session_dir, "/")
    error_message = "Use an absolute ignored private session directory."
  }
}
variable "source_sha" {
  type = string
  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.source_sha))
    error_message = "Bind setup to the exact reviewed source SHA."
  }
}
