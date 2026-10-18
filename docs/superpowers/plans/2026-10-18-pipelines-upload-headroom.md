# Project 8 upload and early cleanup repair

The owner approved targeted fixes and verification followed by one fresh complete
acceptance attempt. Preserve the closed p8-1018c evidence and all existing bounds:
240 upload seconds, 200 requests/index, 500 documents and 12 MiB/request, one HTTP
request in flight, no upload replay, no new embeddings or models. This plan does
not reset the stopped attempt or reuse its source-bound plans and receipts.

## 1. Remove vector serialization overhead

- Modify `eval/app/delivery_search/client.py`, `eval/pyproject.toml`,
  `eval/requirements.txt`; test `eval/tests/delivery_search/test_upload_batches.py`.
- RED: feed memory-mapped float32 vectors to the real batcher and require exact
  float32 bit round trips, Unicode preservation and finite-only values. The old
  JSON encoder refuses ndarray. Preserve existing count/byte/deadline/replay tests.
- GREEN: use pinned orjson 3.13.0 NumPy serialization for ndarray document vectors,
  converting memmap views to ndarray without changing numeric precision. Leave
  canonical authority/evidence hashing and ordinary document encoding unchanged.
- Verify the complete real corpus on Python 3.12/NumPy 2.5.3, two CPU affinity,
  with delayed responses. Compare every transmitted vector to the frozen bits.

## 2. Retain bounded upload diagnostics

- Modify the same client and its tests. Add a create-only adjacent metrics file
  with confirmed document count, requests/bytes started, encoding/HTTP/elapsed
  seconds and fixed outcome/check. No corpus, response, URL or credential text.
- RED: deadline refusal must leave useful counts/timing and no count poll, and
  success must record all documents. A failed diagnostic write must preserve the
  original refusal. GREEN: collect numeric counters around existing boundaries.
- Verify existing pipeline artifact publication includes this stage directory.
- `scripts/pipelines/run.py` copies metrics from the private work directory into
  its stage artifact directory in a finally block. Test the failing-upload path
  in `tests/infra/test_pipelines_runtime.py`. The delivery agent requirements input,
  generated hash lock and versions.json digest also pin the new dependency.

## 3. Enforce early restoration ordering

- Modify `scripts/pipelines/cleanup.py`, `scripts/pipelines/watchdog.py`,
  `docs/runbook-pipelines.md`; test `tests/infra/test_pipelines_cleanup.py` and
  `tests/infra/test_pipelines_quiescence.py`.
- RED: an owner stop before the cleanup deadline with no verified browser
  acknowledgement must not reserve or start deletion or a native cleanup helper.
- GREEN: require acknowledgement before early dispatch and early execution;
  return pending without consuming cleanup intent on direct early execution.
  At the original due time, preserve bounded physical cleanup even without ack,
  retaining the failed evidence predicate rather than waiting indefinitely.
- Verify early verified cleanup, acknowledgement failure, due-time fallback and
  remaining-plan continuation all behave correctly.

## Verification and delivery

Run all eval and infra tests, review the branch independently, and prepare one
fresh source-bound full acceptance packet within the previously approved scope.
No paid resource or queue starts until targeted checks pass. Use the human's
existing proceed instruction for this retry; request only indispensable credential
handoff if necessary. Never recreate live-status.txt.

## Review focus

Memmap subclass serialization; non-finite conversion to JSON null; byte cap with
UTF-8; early helper reservation poisoning due-time fallback; diagnostic write
errors masking the original upload failure. Each is covered by a targeted check.

## Execution evidence

- Native serializer reproduction: old ndarray batch encoding refused; new memory
  mapped float32 and finite-only cases pass. Existing count, byte, concurrency,
  deadline and reservation checks remain enforced.
- Full frozen corpus on Python 3.12.12/NumPy 2.5.3, CPU affinity 0 and 1,
  with 2.0 seconds simulated response delay: 41,825 documents, 84 requests,
  846,904,058 bytes, every vector bit/content/artefact equal. Upload 192.710 seconds
  against 240 seconds; total including validation/setup 201.137 seconds.
- Independent review found a pre-existing metrics-file collision could mask the
  upload refusal. A reproducing test failed before the correction and passes
  afterward; original diagnostic bytes remain untouched. No other findings.
- Due-time physical cleanup remains deliberately available when restoration fails;
  this does not grant a passing cleanup acceptance grade.
