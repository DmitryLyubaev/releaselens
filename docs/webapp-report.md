# Project 6: Webapp evidence

The .NET 10 Razor Pages front end, bootstrap/session declarations, immutable release gate
and fixed evidence harness are implemented for offline review. **Live checks are not run.**
No project-6 Azure deployment, owner-token measurement, paid question, swap, unhealthy
fixture deployment, destroy or purge has occurred as part of offline implementation.

| Check | Verdict | Evidence |
|---|---|---|
| W1 owner identity, answer and Caller metric | not run | none |
| W2 live access and separately bound offline edges | not run | none |
| W3 expired-token refresh and one answer | not run | none |
| W4 separately approved successful promotion | not run | none |
| W5 unhealthy stage and actually rejected promotion | not run | none |
| W6a intended/retained settings and fresh slot sessions | not run | none |
| W6b teardown, purge absence and bootstrap retained | not run | none |

W6: not run. It requires both W6a and W6b to pass.

Offline tests verify application refusal paths, encoding/antiforgery, one bounded gateway
call, privacy guards, mocked Terraform declarations and artifact/release trust rules.
They do not establish Easy Auth token issuance/refresh, runtime availability, scoped Entra
SCM support, swap behavior or any absence result in Azure. Both slots share one front-end
registration and identity. Docker-dependent suites remain CI coverage.

Follow the [session runbook](runbook-webapp.md). Replace this skeleton only after actual
guarded measurements and owner review, retaining failed/not-run checks and live findings.
