# Project 7 agent host

An independent local-state stack for the approved CV demonstration. It reads or changes
no existing stack. Nine new ARM resources plus one Terraform-only PAT readiness gate are
planned. The PAT itself is uploaded privately from an owner-only file, after the new vault
and owner role exist and before either job is created. Terraform never receives its value.

See [the runbook](../../docs/runbook-agents.md) for account checks, DevOps setup, reviewed
publication, explicit live approval, private state paths, observations and scoped cleanup.
Run `terraform init -backend=false`, `terraform validate` and `terraform test` for offline
schema/mock checks. These are not live acceptance evidence.

Apply only through the personal WSL owner runtime using a saved, reviewed plan, private
`TF_DATA_DIR` and explicit ignored local state path. Never share state or raw plan output.
Keep Key Vault purge protection and record soft-deleted vault retention after teardown.
