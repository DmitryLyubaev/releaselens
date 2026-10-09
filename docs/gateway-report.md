# API Management AI gateway: measured test

## Test 1: failover

Verdict: held

45 of 45 after-run requests succeeded, 41 of them answered by the Southeast Asia region.

**Before (direct to the primary) run**
- succeeded 4 of 45; failed 41 of 45
- failure statuses: 429 x41
- answered by the primary: 4; by Southeast Asia: 0
- latency, all 45 requests including failures: p50 121 ms, p95 1669 ms

**After (through the gateway) run**
- succeeded 45 of 45; failed 0 of 45
- failure statuses: none
- answered by the primary: 4; by Southeast Asia: 41
- the two region signals (`x-ms-region` and `x-releaselens-backend`) disagreed on 0 of 45 responses that carried either
- latency, all 45 requests including failures: p50 1280 ms, p95 1704 ms

Latencies are descriptive only; they decide nothing.

"Answered by Southeast Asia" means the Southeast Asia account served the request, not that the prompt was processed there: a Global Standard deployment may process a prompt in any Azure region.

## Test 2: budgets and access

- B1: fail (60 requests were sent and none was refused with a 429)
- B2: pass (request 50 got a 403 after 16 minute-budget waits; 48300 tokens recorded today before the 403 (36750 earlier, 11550 in this run))
- B3: pass (entered by hand from the workflow run; this harness did not measure it)
- B4: pass (the no-token call and the wrong-audience call both got 401)
- B5: pass (within 2%: deploy 350 vs 350; owner 48300 vs 48300; deploy: from the workflow log, entered by hand)

**Not tested live.** A valid token for the gateway that lacks `Gateway.Invoke` was not tested live: there is no second user to test it with, and none was created. With assignment required on the Entra app, such a token cannot be issued at all.

## What the run used

- The region signal frozen before the first measured request (freeze.json): `x-ms-region`.
- The commit: `344ed07691383e3513555cd0fc19e72a23defd90`, HEAD when this report was made. Measured commands refuse a tree with uncommitted changes, so it is the commit that ran unless one was made since.
- Callers appear as labels. No identifier, hostname or account name is in this report.
