variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
}

variable "location" {
  type        = string
  description = "Azure region of the gateway (spec §3.2)."
  default     = "australiaeast"
}

# The same account init's -backend-config names. The bootstrap's outputs are read from its
# state there, so no identifier is copied by hand.
variable "tfstate_storage_account" {
  type        = string
  description = "Name of the state storage account: terraform -chdir=../bootstrap output -raw tfstate_storage_account."
}

# API Management requires a publisher email. A variable, from the git-ignored tfvars, so no
# address is in the repository.
variable "publisher_email" {
  type        = string
  description = "Email address API Management sends its service notifications to."
}

# Low on purpose (spec §4.1): nothing real calls the gateway, and low limits make the budget
# checks quick and cheap. Every caller has the same limits and its own counter.
variable "tokens_per_minute" {
  type        = number
  description = "Each caller's token budget per minute. Over it, the gateway returns 429 with Retry-After."
  default     = 10000
}

variable "tokens_per_day" {
  type        = number
  description = "Each caller's token budget per day. Over it, the gateway returns 403."
  default     = 50000
}

# Revision 2 is made current only after it has been called at ;rev=2 in the smoke test
# (spec §3.2, §4.3).
variable "release_revision_2" {
  type        = bool
  description = "Make revision 2 of the API current. Set it to true only after revision 2 has passed the smoke test."
  default     = false
}

# Project 10: one event per gateway call to Event Hubs, for the Fabric real-time session. Off by
# default, so every other session applies the gateway exactly as before: no logger, no named
# value of its own, and each policy is its file without the marker comment (see the README).
variable "event_hub_logging" {
  type        = bool
  description = "Send one event per gateway call to Event Hubs, through the logger p10-eventhub. Off by default."
  default     = false
}

# Names, not hosts: the logger's endpoint is the namespace's name plus .servicebus.windows.net.
# From the git-ignored tfvars, copied from the Fabric repo's Event Hubs stack, so no name is in
# the repository.
variable "event_hub_namespace" {
  type        = string
  description = "Name of the Event Hubs namespace the logger sends to. Required when event_hub_logging is on."
  default     = ""

  validation {
    condition     = !var.event_hub_logging || can(regex("^[A-Za-z][A-Za-z0-9-]{4,48}[A-Za-z0-9]$", var.event_hub_namespace))
    error_message = "With event_hub_logging on, event_hub_namespace must be the namespace's name (6 to 50 letters, digits and hyphens), not its host."
  }
}

variable "event_hub_name" {
  type        = string
  description = "Name of the event hub the logger sends to. Required when event_hub_logging is on."
  default     = ""

  validation {
    condition     = !var.event_hub_logging || can(regex("^[A-Za-z0-9]([A-Za-z0-9._-]{0,254}[A-Za-z0-9])?$", var.event_hub_name))
    error_message = "With event_hub_logging on, event_hub_name must be the event hub's name."
  }
}

# The event's session field. The policy puts it inside a string literal, so it is a plain slug.
# "replay" is reserved for generated events, which never feed a published figure.
variable "p10_session" {
  type        = string
  description = "The session name every event carries, for example p10-2026-11-12. Required when event_hub_logging is on; never \"replay\"."
  default     = ""

  validation {
    condition     = !var.event_hub_logging || (can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", var.p10_session)) && lower(var.p10_session) != "replay")
    error_message = "With event_hub_logging on, p10_session must be a slug of letters, digits, '.', '_' or '-' (at most 64), and not \"replay\"."
  }
}

# Object IDs are identifiers, so this comes from the git-ignored tfvars and is a secret named
# value: the policy maps the caller's oid to its label, and no oid leaves the gateway.
variable "caller_labels_json" {
  type        = string
  description = "A JSON object mapping each known caller's Entra object ID to its label: owner, deploy, webapp or other. Any other caller is logged as other."
  default     = "{}"
  sensitive   = true

  validation {
    condition = can(keys(jsondecode(var.caller_labels_json))) && try(alltrue([
      for oid, label in jsondecode(var.caller_labels_json) : contains(["owner", "deploy", "webapp", "other"], label)
    ]), false)
    error_message = "caller_labels_json must be a JSON object whose values are owner, deploy, webapp or other."
  }
}
