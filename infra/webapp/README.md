# Project 6 session stack

The owner applies this stack after bootstrap and the gateway. It owns one Linux
S1 instance in Australia East, a main site and staging slot, authentication,
publishing restrictions, sticky environment settings and a Website Contributor
assignment scoped to the site for the existing deployment identity. Billing
continues until the plan is destroyed. The session target is four hours.

Bootstrap retains the registration, identity and private state container. The
two slots share that registration and identity. They cannot prove isolation
between different authentication registrations. The new identity can assert
the Easy Auth client identity; model requests use only the owner's delegated
gateway token.

Both slots require Easy Auth and HTTPS. Only the signed-out page and exact
static asset paths are public. Application code permits the deployment identity
to read health only. Basic SCM and FTP credentials are disabled. No secret,
certificate, retrieval service, system identity or automatic swap is created.

`Web__*`, `ASPNETCORE_ENVIRONMENT` and
`OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID` stay with their slots. Version comes
from the compiled assembly and moves with the ZIP. `WEBSITE_RUN_FROM_PACKAGE=1`
is constant on both slots. The release owns SCM's `SitePackages` files and its
package-selection manifest, which this ARM configuration never sets. A later
Terraform plan therefore has no seed package URI or version to restore.
Actual package selection and swaps still need live verification.

Use Terraform only in WSL, with a dedicated
`TF_DATA_DIR=$HOME/tfdata/webapp`. Initialize the Entra-authenticated backend
using the privately captured state account. Never publish raw outputs, plans,
errors, account metadata or hostnames. No actual tfvars or state is needed for
offline tests. The full account guard and session procedure are in
[the runbook](../../docs/runbooks/webapp-session.md).

Each live plan/apply, workflow deployment, swap, destroy and gateway soft-delete
purge requires its own owner approval. The existing nightly app destroy does
not remove this stack. Finish the session with approved teardown and W6b
absence checks; do not leave the S1 plan running overnight.

Offline validation uses clean copies, `init -backend=false`, `fmt`, `validate`
and mocked `terraform test`. It proves declared configuration, not cloud
availability, token issuance/refresh, slot warmup or Entra deployment at the
narrow permission scope. Keep those unknowns explicit until measured.
