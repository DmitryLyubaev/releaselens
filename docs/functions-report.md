# Azure Functions: ingestion and a search tool: measured checks

Commit `9c475572e8f1475316b81a33d4c0d14255aa5da9`. 8 of 8 checks passed.

Each pass rule was fixed in the spec (section 7) before any live run. A failed check is a result, and is reported as one.

## Ingestion

- I1: pass (a new artefact becomes searchable): 5 of 5 searchable within 120 s of their upload: median 11 s, maximum 11 s (seen at each poll, so at most 5 s late); 0 of 15 polls failed with a transient error (counted as not seen yet)
- I2: pass (a changed artefact replaces itself): exactly 1 chunks under the new keys, with new text and without the cut-away text; 5 old keys gone, 5 s after the upload; 0 of 2 polls failed with a transient error (counted as not seen yet)
- I3: pass (duplicates are harmless): 5 chunks and the same keys 60 s after the second upload as after the first
- I4: pass (broken input goes to the poison queue): a poison message for the upload came 159 s after it; the index gained no documents (41821 before and after)

## The tool

- T1: pass (the tool runs the same search as project 2): search_corpus is in the tool list; 10 of 10 questions returned the same top 5 artefacts, in order, as a direct query
- T2: pass (the gateway refuses strangers): the gateway refused strangers: the call with no token got 401 and the call with a wrong-audience token got 401
- T3: pass (no way round the gateway): the tool app refused direct calls: the direct call with no token got 401 and the direct call with the gateway token got 401
- T4: pass (the per-caller limit holds): 11 of 30 calls at once got a 429 with Retry-After; the Tool Calls metric shows 21 calls under owner in the burst's window, at least the 21 expected (19 let through and 2 for the handshake)

T3 sends the call straight to the tool app twice: with no token, and with the owner's gateway token. The owner is not assigned to `releaselens-search-tool`, so the owner cannot get a token for the tool app's own audience; a call carrying a token for that audience was not tested.

## Notes on the run

- **Two commits.** I1 to I4 and T1 ran from commit `9748b95`. T2, T3 and T4 ran from `9c47557`,
  after a fix to how the harness's MCP client reads a refusal: it now reads the status line first.
  That fix does not touch the ingestion checks or T1's path.
- **T2 and T3 were first recorded as "no response".** On Windows, the owner's TLS-inspecting
  antivirus held back the bodies of short replies, so the client waited for the 401s' bodies. The
  same calls from WSL got 401 in about 0.2 s. The fix made the client raise a refusal from its
  status line, and T2 and T3 then passed as above.
- **T4.** The rate limit let 19 calls through, plus the 2 handshake requests: 21 counted against a
  limit of 20 a minute. The `Tool Calls` metric rows were stamped at second 1 and second 54 of
  their minute, not at second 0 as the harness assumed. T4's window still caught its row, and there
  was no shortfall.
- **The Claude Code demo.** Asked headless, with only `search_corpus` allowed, which release or
  pull request fixed the Pydantic "StringConstraints() takes no arguments" error when importing
  `OpenAIChatCompletion`, Claude Code called `search_corpus` once (top 10) and cited `issue:4430`,
  `commit:cdec3b9…` and `release:python-0.4.5.dev`.
