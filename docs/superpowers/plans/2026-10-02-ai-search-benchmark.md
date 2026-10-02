# Retrieval benchmark on Azure AI Search, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pre-registered six-arm retrieval benchmark from the spec, and the owner-gated
runbook that measures and publishes it.

**Architecture:**
- **Two Worker commands give Python the data and the app's own retrieval.** Small C# pieces in
  `ReleaseLens.Storage`, wired into two new commands: one exports the corpus and its
  pull-request-to-commit links as JSON lines; the other runs the app's real `HybridRetriever`
  (S1) and an exact BGE search (E1) over a frozen question file.
- **The rest lives in a new package, `eval/app/retrieval/`:**
  - writing and freezing the questions
  - embedding the corpus keylessly with two Azure deployments
  - building an AI Search index
  - running the network arms (E2, E3, S2, S3)
  - scoring, and rendering the report, reusing plan 3's bootstrap and decision rule
- **Terraform:** the bootstrap stack gains the embedding deployments, a state container and its
  role. A new owner-applied stack, `infra/search`, holds the search service.

**Tech Stack:**
- **C#, .NET 10:** Worker and Storage, with xUnit and the existing Testcontainers Postgres fixture
- **Python 3.14:** `eval/`, with httpx, numpy, azure-identity, anthropic and pytest
- **Terraform 1.15.8 with azurerm 5.x:** run in WSL only, with mocked `terraform test`

**Spec:** `docs/superpowers/specs/2026-10-02-azure-ai-search-benchmark-design.md`, approved
2026-10-02.

## Global Constraints

- **The design is fixed in advance (spec §3–§5).** Implement it as written and change none of it.
- **Sample (spec §3.2):**
  - 300 artefacts, seed `20261002`, stratified by `entity_type` in proportion to the distinct
    artefacts in `evidence_chunks`, using largest remainder
  - an artefact is skipped when its chunks' `token_count` totals fewer than 40
  - Claude sees at most 1,500 tokens of an artefact's chunks, in order
- **The writer (spec §3.3):** Claude Sonnet 5 (`claude-sonnet-5`), one question per artefact,
  returning JSON `{"question": ..., "why_unique": ...}`.
- **The checks (spec §3.4).** Reject a question if:
  - more than 50% of its content words appear in the target's text. Content words are lower-cased
    words of 4 or more letters, not in a fixed English stop-word list.
  - it contains the target's key: a SHA prefix of 7 or more characters, `#<number>`, the tag, or
    the URL
  - it has fewer than 6 words or more than 40
  - the reply is not valid JSON

  A rejected artefact gets one rewrite. After that, the next seeded draw of the same type replaces
  it. The final count is exactly 300.
- **The spot-check (spec §3.5):**
  - 30 questions, with a fixed seed
  - the owner marks each one `fine`, `ambiguous` or `wrong`
  - more than 3 that are not `fine` means the set is regenerated
- **Freezing (spec §3.6):** `eval/retrieval/questions.jsonl`, plus a manifest holding the seed,
  the prompt, the model, the date, the rejection counts, the spot-check marks and the file's
  SHA-256.
- **The arms (spec §4).** All six search the same 41,825 chunks and return their top 50 chunks:
  - **E1, BGE exact:** cosine over the stored vectors, with BGE's query prefix
  - **E2:** `text-embedding-3-small`, exact
  - **E3:** `text-embedding-3-large`, exact, full 3,072 dimensions
  - **S1:** `HybridRetriever`
  - **S2:** AI Search hybrid with `-small`
  - **S3:** S2 with `queryType: semantic`
- **Scoring (spec §5.1):**
  - **Collapse:** chunks collapse to artefacts by first appearance.
  - **top-1:** whether the first artefact is the target.
  - **Reciprocal rank:** 0 when the target isn't within the 50 chunks.
  - **Lenient top-1:** also accepts a pull request's merge commit, and the reverse.
  - **Margin:** the score of the first artefact minus the score of the best different artefact,
    within the arm only, on hits.
- **Decision rule (spec §5.4).** It covers C1 = E3−E1, C2 = S3−S1 and C3 = S3−S2:
  - **The difference:** the paired difference in top-1 accuracy over the same questions.
  - **The interval:** a 95% bootstrap over questions, with 10,000 resamples and seed `20261002`,
    nearest rank, and values settled to 12 places.
  - **A difference** is declared when it is at most −0.05 or at least +0.05 and the interval
    excludes zero. Otherwise the verdict is `inconclusive at 300 questions`.
  - **No correction** is made for running three comparisons, and the write-up says so.
- **Determinism (spec §5.2):** the first 30 questions are run a second time on every arm. More
  than 3 top-1 changes on an arm is a caveat on that arm.
- **Deployments (spec §6.1):**
  - `releaselens-embed-small` (`text-embedding-3-small`, version `1`) and
    `releaselens-embed-large` (`text-embedding-3-large`, version `1`)
  - `GlobalStandard`, capacity 350, `NoAutoUpgrade`
- **The search stack (spec §6.2):**
  - resource group `rg-releaselens-search`
  - a search service on `basic`, in `australiaeast`, with 1 replica and 1 partition
  - `local_authentication_enabled = false`, and the semantic search SKU `free`
  - the owner's roles, on the service only: `Search Service Contributor` and
    `Search Index Data Contributor`
  - its state in a new container, `tfstate-search`, on which bootstrap grants the owner
    `Storage Blob Data Contributor`
- **Authentication (spec §7):**
  - Python calls Azure as the owner, through the Azure CLI
  - embeddings use the scope `https://ai.azure.com/.default`
  - AI Search uses the scope `https://search.azure.com/.default`
  - no API key is used anywhere, and none is written to any file
- **No network in tests.** Fakes stand in for every Azure, Anthropic and search call.
- **Commits:**
  - repo-local identity, never `--author`, never `git config --global`
  - every message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  - branch `feat/ai-search-benchmark`
  - `main` requires the `build-and-test` and `terraform-plan` checks
- **Terraform runs only in WSL.** Never run `plan` or `apply` against Azure in a task.

## Review Focus

1. **Throttling while embedding the corpus.** A 429 or a dropped connection partway through
   embedding the corpus resumes from the last saved batch, and never pays twice for chunks
   already embedded. (Task 3 `test_embedding_resumes_after_a_failed_batch`)
2. **A failure on one question.** When one arm errors on a question (a search 5xx, a timeout, or
   a ranker billing error once the free plan is used up), that arm records an error for that
   question, and the question drops out of that arm's pairs. It is never scored as a miss, and the
   report counts the drops. (Task 5 `test_an_errored_question_is_dropped_not_missed`;
   Task 6 `test_errored_pairs_are_dropped_and_counted`)
3. **A question file changed after freezing.** Measurement refuses to start, naming the
   mismatch. (Task 4 `test_load_frozen_refuses_an_edited_file`)
4. **A target beyond the 50 chunks.** It scores top-1 0 and reciprocal rank 0, and is not an
   error. (Task 6 `test_target_outside_the_list_scores_zero`)
5. **A bootstrap change that disturbs the deployed chat model.** The plan must show the two
   embedding deployments and the state container being added, with nothing changed on the chat
   deployment or the account. (Task 7 test `chat_deployment_unchanged`)

---

### Task 1: Storage support for the export and the exact BGE search

**Files:**
- Create: `src/ReleaseLens.Storage/Retrieval/CorpusExporter.cs` and `src/ReleaseLens.Storage/Retrieval/ExactVectorSearch.cs`
- Test: `tests/ReleaseLens.Storage.Tests/CorpusExporterTests.cs` and `tests/ReleaseLens.Storage.Tests/ExactVectorSearchTests.cs` (both use the existing `PostgresFixture`)

**Interfaces:**
- **Produces:**
  - `public sealed record ExportedChunk(long ChunkId, string Artefact, string EntityType, string EntityKey, int ChunkIndex, int TokenCount, string Content)`. `Artefact` is `"{EntityType.ToWireName()}:{entity_key}"`.
  - `public sealed record ExportedLink(string PullRequest, string Commit)`, both in `type:key` form.
  - `CorpusExporter.ExportChunksAsync(TenantScope scope, CancellationToken) -> IAsyncEnumerable<ExportedChunk>`, ordered by `chunk_id`.
  - `CorpusExporter.ExportLinksAsync(TenantScope scope, CancellationToken) -> Task<IReadOnlyList<ExportedLink>>`: one link per merged pull request whose `merge_commit_sha` names a commit in `commits`.
  - `ExactVectorSearch.SearchAsync(TenantScope scope, float[] queryVector, int k, CancellationToken) -> Task<IReadOnlyList<(long ChunkId, string Artefact, double Score)>>`. It is an exact cosine search with the HNSW index bypassed (`set local enable_indexscan = off` inside the transaction). The score is `1 - (embedding <=> @q)`, ordered best first.

- [ ] **Step 1: Write the failing tests**
  - **`ExportChunks_ReturnsEveryChunkOfTheTenantWithItsArtefact`:** seed 2 commits, 1 pull request and 3 chunks. Assert all 3 come back, in `chunk_id` order, with `Artefact` equal to `"commit:<sha>"` or `"pull_request:<n>"`.
  - **`ExportChunks_ExcludesOtherTenants`.**
  - **`ExportLinks_PairsAMergedPullRequestWithItsMergeCommit`:** a pull request whose `merge_commit_sha` names a seeded commit gives one link. One whose commit isn't in the corpus gives none, and so does one with a null SHA.
  - **`ExactSearch_ReturnsTheTrueNearestNeighbours`:** seed 5 vectors with known cosines to the query. The order and the scores (to 1e-6) match a brute-force computation in the test.
  - **`ExactSearch_DoesNotUseTheHnswIndex`:** run `EXPLAIN` on the SQL the class uses, and assert the plan holds no `embeddings_hnsw_idx`. Expose that SQL as an `internal const` for the test.

- [ ] **Step 2: Run them, and see them fail**

Run: `dotnet test tests/ReleaseLens.Storage.Tests --filter "FullyQualifiedName~CorpusExporter|FullyQualifiedName~ExactVectorSearch"`. These need Docker; where Docker is unavailable on Windows, CI runs them. Expected: FAIL to compile, because the types don't exist yet.

- [ ] **Step 3: Implement both classes,** following `HybridRetriever`'s use of `TenantScope` and Dapper.

- [ ] **Step 4: Run the tests and the build**

Run: `dotnet build -c Release`, which must give 0 warnings, then the filter above. Expected: PASS, or "requires Docker" locally, with CI as the gate.

- [ ] **Step 5: Commit**

`git commit -m "feat(storage): export the corpus and its merge links, and an exact BGE search for the benchmark"`

---

### Task 2: Worker commands `export-corpus` and `retrieve`

**Files:**
- Modify: `src/ReleaseLens.Worker/Program.cs` (two `case` branches)
- Create: `src/ReleaseLens.Storage/Retrieval/BenchmarkRunner.cs`. It lives in Storage, so the existing Storage test project can test it, because no Worker test project exists.
- Test: `tests/ReleaseLens.Storage.Tests/BenchmarkRunnerTests.cs`

**Interfaces:**
- **Consumes:** Task 1's types, `HybridRetriever`, and `IEmbedder.EmbedQuery`.
- **Produces, the command-line contract:**
  - `dotnet run --project src/ReleaseLens.Worker -- export-corpus <dir>` writes `<dir>/chunks.jsonl` (one `ExportedChunk` per line, camelCase) and `<dir>/links.jsonl` (one `ExportedLink` per line).
  - `dotnet run --project src/ReleaseLens.Worker -- retrieve <hybrid|bge-exact> <questions.jsonl> <out.jsonl>` takes input lines `{"qid": str, "question": str}`, which is the frozen file's shape (Task 4). It writes one line per question: `{"qid", "arm": "S1"|"E1", "hits": [{"chunkId", "artefact", "score"}…50], "ms": float, "error": str|null}`.
    - **`hybrid`:** `HybridRetriever.RetrieveAsync` with `K = 50` and the request's defaults otherwise. The score is `BlendedScore`.
    - **`bge-exact`:** `ExactVectorSearch.SearchAsync` with `k = 50`.
  - **Timing:** `ms` covers embedding the query plus the search, measured with `Stopwatch`, and excludes process start.
  - **One bad question doesn't stop the run:** an exception on one question gives that line an `error` and the run continues.
  - **The tenant:** `Tenant:Slug`, as the other commands use it.
- `BenchmarkRunner.RunRetrieveAsync(string mode, TextReader questions, TextWriter output, Func<string, CancellationToken, Task<IReadOnlyList<(long ChunkId, string Artefact, double Score)>>> search, CancellationToken)` is public and testable. Input lines may carry fields beyond `qid` and `question`, such as the frozen file's `target`; ignore them. `Program.cs` composes `search` per mode:
  - **`hybrid`:** embed with `IEmbedder.EmbedQuery`, then call `HybridRetriever.RetrieveAsync`.
  - **`bge-exact`:** embed the same way, then call `ExactVectorSearch.SearchAsync`.

  The embedding sits inside the delegate, so `ms` includes it.

- [ ] **Step 1: Write the failing tests**
  - **`Retrieve_WritesOneLinePerQuestionWithFiftyHits`:** a fake `search` returns 60 hits. The output has the qids in input order, 50 hits each, and `ms >= 0`.
  - **`Retrieve_RecordsAnErrorAndContinues`:** the fake throws on the second qid. Line 2 has an `error` and `hits == []`, and line 3 is fine.
  - **`Retrieve_RejectsAnUnknownMode`:** `"approximate"` throws `ArgumentException`, naming the two valid modes.
- [ ] **Step 2: Run them, and see them fail.**
- [ ] **Step 3: Implement it, and wire the two `case` branches.** Use `System.Text.Json` with the camelCase policy.
- [ ] **Step 4: Run the tests, and build with 0 warnings. Expect PASS.**
- [ ] **Step 5: Commit**

`git commit -m "feat(worker): export-corpus and retrieve commands for the retrieval benchmark"`

---

### Task 3: Python foundations: corpus loading, keyless tokens, and the corpus embedder

**Files:**
- Modify: `eval/requirements.txt` and `eval/pyproject.toml`. Add `numpy` and `azure-identity`, pinned to versions that install on Python 3.14 on Windows and on Ubuntu.
- Create: `eval/app/retrieval/__init__.py`, `corpus.py`, `azure_auth.py` and `embed.py`
- Test: `eval/tests/retrieval/test_corpus.py` and `eval/tests/retrieval/test_embed.py`

**Interfaces:**
- `corpus.py`:
  - `@dataclass(frozen=True) class Chunk(chunk_id: int, artefact: str, entity_type: str, entity_key: str, chunk_index: int, token_count: int, content: str)`
  - `load_chunks(path: Path) -> list[Chunk]`
  - `load_links(path: Path) -> dict[str, frozenset[str]]`, symmetric, so each side maps to the other
- `azure_auth.py`: `class TokenSource(scope: str, tenant_id: str)` with `.token() -> str`. It wraps `AzureCliCredential(tenant_id=…)` and caches until 5 minutes before expiry.
- `embed.py`:
  - `embed_corpus(chunks: list[Chunk], *, base_url: str, deployment: str, tokens: TokenSource, out_dir: Path, batch_size: int = 64, client: httpx.Client | None = None) -> EmbeddingRun`
  - **The call:** `POST {base_url}embeddings` with JSON `{"model": deployment, "input": [...]}` and a bearer token.
  - **What it saves:** `{out_dir}/{deployment}.npy` (float32, one row per chunk, in `chunks` order), `{deployment}.ids.json` (the `chunk_id` order) and `{deployment}.progress.json` (batches done).
  - **The result:** `EmbeddingRun(deployment, vectors_path, ids_path, tokens_billed: int, batches: int)`. `tokens_billed` is the sum of `usage.prompt_tokens`.
  - `embed_query(text, *, base_url, deployment, tokens, client=None) -> tuple[np.ndarray, int]` returns the vector and its tokens.
- Add `eval/.gitignore` entries for `retrieval-data/`, which holds exports, vectors and arm outputs.

- [ ] **Step 1: Write the failing tests,** with `httpx.MockTransport` throughout:
  - **`test_load_links_is_symmetric`**
  - **`test_embedding_saves_rows_in_chunk_order`:** 130 chunks with a batch of 64 makes 3 calls, and the saved array has shape (130, d).
  - **`test_embedding_resumes_after_a_failed_batch`** (Review Focus 1): the transport fails batch 2 with a 429, a `Retry-After: 0`, and then a connection error past the retry limit. The first call raises. A second call, given the same `out_dir`, sends only batches 2 and 3; count the requests to prove it. The final array equals a run with no faults.
  - **`test_429_honours_retry_after_then_succeeds`:** at most 5 retries, with no real sleeping in the test, by injecting `sleep`.
  - **`test_no_key_header_is_ever_sent`:** every request has `Authorization: Bearer …`, and none has `api-key`.
- [ ] **Step 2: Run them, and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/retrieval -q` from `eval/`.

- [ ] **Step 3: Implement it.** Write the vectors through a memory-mapped `.npy` opened with `np.lib.format.open_memmap`, so a resume writes into the same file.
- [ ] **Step 4: Run all the eval tests and expect PASS:** `.venv/Scripts/python.exe -m pytest -q`.
- [ ] **Step 5: Commit**

`git commit -m "feat(eval): load the exported corpus, get keyless tokens, and embed the corpus resumably"`

---

### Task 4: The question set: sample, write, check, spot-check and freeze

**Files:**
- Create: `eval/app/retrieval/questions.py` and `eval/app/retrieval/stopwords.txt` (a fixed English stop-word list, committed)
- Test: `eval/tests/retrieval/test_questions.py`

**Interfaces:**
- `@dataclass(frozen=True) class Artefact(artefact: str, entity_type: str, text: str, token_count: int, url_hints: tuple[str, ...])`. `text` is the chunks in order, truncated to 1,500 tokens by `token_count`.
- `sample_artefacts(chunks: list[Chunk], *, n: int = 300, seed: int = 20261002, min_tokens: int = 40) -> SampleStream`
  - The quotas come from largest remainder over the distinct artefacts per type.
  - `SampleStream.draw(entity_type) -> Artefact` yields the next seeded artefact of that type that clears `min_tokens`. One `random.Random(seed)` shuffle per type gives the order.
- `content_words(text: str) -> set[str]`, and `check(question: str, target: Artefact) -> list[str]`. The latter returns the reasons for rejection, empty when the question passes:
  - `"copies its target: 0.62 of content words"`
  - `"contains the target's key"`
  - `"too short"` and `"too long"`
- `write_question(target: Artefact, client) -> Draft(question: str | None, why_unique: str | None, raw: str, input_tokens: int, output_tokens: int)`. It uses `anthropic` with `model="claude-sonnet-5"` and `max_tokens=300`. The prompt is a module constant `PROMPT`.
- `build_set(stream, client) -> list[Question]`, where `Question(qid: str, question: str, target: str, entity_type: str)`, and `qid` is `q001` to `q300` in draw order. It applies one rewrite, then replacement from the same type. It returns the set plus `rejections: dict[str, int]` and the token totals.
- `spot_check_sheet(questions, artefacts, *, seed: int = 20261002, n: int = 30) -> list[dict]`, and `apply_marks(sheet, marks: dict[str, str]) -> SpotCheck(fine: int, not_fine: int, passes: bool)`. It passes when `not_fine <= 3`.
- `freeze(questions, manifest: dict, path: Path) -> str` writes the JSON lines plus `path.with_suffix(".manifest.json")` holding the SHA-256, and returns the hash.
- `load_frozen(path: Path) -> list[Question]` raises `ValueError` naming both hashes when the file's SHA-256 differs from the manifest's.

- [ ] **Step 1: Write the failing tests,** with a fake Anthropic client as in `tests/test_judge.py`:
  - **`test_quotas_use_largest_remainder_and_total_300`:** on counts 2,921, 3,805, 7,121 and 276, the quotas are 62, 81, 151 and 6.
  - **`test_sampling_is_reproducible_and_skips_thin_artefacts`:** the same seed gives the same draws, and an artefact with 39 tokens is never drawn.
  - **`test_check_rejects_copied_wording`:** a question built from the target's own words, more than 0.5 overlap, is rejected; a paraphrase passes.
  - **`test_check_rejects_keys`:** a 7-character SHA prefix, `#14111`, a tag like `dotnet-1.79.0` and the URL are all rejected.
  - **`test_check_rejects_length`:** 5 words and 41 words are both rejected.
  - **`test_rejected_artefact_is_rewritten_once_then_replaced`:** with a fake that returns copied wording twice, then a good question, there are 2 calls for the first artefact, the third question comes from a replacement artefact of the same type, and the total stays exact.
  - **`test_invalid_json_counts_as_a_rejection`**
  - **`test_spot_check_stop_rule`:** 3 not fine passes, and 4 fails.
  - **`test_load_frozen_refuses_an_edited_file`** (Review Focus 3): freeze, change one byte, and `load_frozen` raises with both hashes in its message.
- [ ] **Step 2: Run them, and see them fail.**
- [ ] **Step 3: Implement it.** `PROMPT` states the spec §3.3 instruction in plain words. Treat the stop-word file as data.
- [ ] **Step 4: Run all the eval tests and expect PASS.**
- [ ] **Step 5: Commit**

`git commit -m "feat(eval): write, check, spot-check and freeze the 300 benchmark questions"`

---

### Task 5: The arms and the AI Search index

**Files:**
- Create: `eval/app/retrieval/arms.py` and `eval/app/retrieval/search_index.py`
- Test: `eval/tests/retrieval/test_arms.py` and `eval/tests/retrieval/test_search_index.py`

**Interfaces:**
- **The types:** `@dataclass(frozen=True) class Hit(chunk_id: int, artefact: str, score: float)` and `@dataclass class ArmResult(qid: str, arm: str, hits: list[Hit], ms: float, error: str | None, query_tokens: int = 0)`.
- `exact_cosine(vectors: np.ndarray, ids: list[int], artefacts: dict[int, str], query: np.ndarray, k: int = 50) -> list[Hit]`: normalise, dot product, `np.argpartition` then sort, best first.
- `run_embedding_arm(arm: str, questions, *, vectors_path, ids_path, artefacts, embed) -> list[ArmResult]`, where `embed(text) -> (vector, tokens)`. This covers E2 and E3, and the timing includes the embedding call.
- `read_worker_output(path: Path) -> list[ArmResult]`: E1 and S1, from Task 2's JSON lines.
- `search_index.py`:
  - **The constants:** `INDEX_NAME = "releaselens-chunks"` and `API_VERSION = "2026-04-01"`.
  - `create_index(endpoint, tokens, client=None)` sends the spec §4 schema. The fields are `chunk_id` as a key string, `artefact` as filterable, `content` as searchable with the analyzer `en.lucene`, and `vector` with 1,536 dimensions, cosine and HNSW. It has a semantic configuration `default` with `content`.
  - `upload(chunks, vectors_path, ids_path, endpoint, tokens, client=None, batch=500)`.
  - `search(question, vector, *, semantic: bool, endpoint, tokens, client=None) -> list[Hit]`:
    - **The request:** `search` text plus one vector query (`k: 50`), `top: 50`, `select: chunk_id,artefact`, and with `semantic` set, `queryType: semantic` and `semanticConfiguration: default`.
    - **The score:** `@search.rerankerScore` when semantic, else `@search.score`.
- `run_search_arm(arm: "S2"|"S3", questions, *, query_vectors: dict[str, np.ndarray], …) -> list[ArmResult]`. S2 and S3 reuse E2's question vectors, so there are no extra embedding calls. Any non-2xx reply or exception becomes that question's `error`. A billing error is kept verbatim in `error`.

- [ ] **Step 1: Write the failing tests,** with `httpx.MockTransport`:
  - **`test_exact_cosine_matches_brute_force`:** random vectors with a fixed seed; the top 50 ids and scores (to 1e-6) match a sorted full computation.
  - **`test_search_sends_hybrid_and_semantic_bodies`:** assert the two request bodies exactly, the scope bearer, and no `api-key` header.
  - **`test_semantic_uses_reranker_score`**
  - **`test_an_errored_question_is_dropped_not_missed`** (Review Focus 2, the arm half): a 503, a timeout and a 4xx billing-error body each give `error` set and `hits == []`, and the run continues.
  - **`test_create_index_schema`:** the PUT body equals the spec's schema.
  - **`test_upload_batches_and_fields`:** 1,001 chunks with a batch of 500 make 3 requests, each document holding `chunk_id` as a string, `artefact`, `content` and a vector of 1,536 dimensions.
  - **`test_read_worker_output`:** it parses Task 2's contract, including an `error` line.
- [ ] **Step 2: Run them, and see them fail.**
- [ ] **Step 3: Implement it,** using `TokenSource` for every Azure call.
- [ ] **Step 4: Run all the eval tests and expect PASS.**
- [ ] **Step 5: Commit**

`git commit -m "feat(eval): the six retrieval arms and the keyless AI Search index"`

---

### Task 6: Scoring, the comparisons, the report and the command line

**Files:**
- Modify: `eval/app/analysis.py`. `verdict` gains a keyword `threshold: float = DIFFERENCE_THRESHOLD`, and `_settled` becomes public as `settled`, keeping a `_settled` alias. Existing callers are unchanged.
- Create: `eval/app/retrieval/score.py`, `report.py` and `__main__.py` (the CLI), plus `eval/retrieval/README.md` (how to run, with no results)
- Test: `eval/tests/retrieval/test_score.py`, `eval/tests/retrieval/test_report.py` and a regression in `eval/tests/test_analysis.py`

**Interfaces:**
- `score.py`:
  - **The constants:** `THRESHOLD = 0.05`, `SEED = 20261002` and `COMPARISONS = (("E3", "E1"), ("S3", "S1"), ("S3", "S2"))`.
  - `collapse(hits: list[Hit]) -> list[tuple[str, float]]` returns the artefacts in first-appearance order, each with the score of its first chunk.
  - `score(result: ArmResult, target: str, links) -> QuestionScore(top1: int, rr: float, lenient_top1: int, margin: float | None)`, or `None` when `result.error` is not None.
  - `compare_top1(scores: dict[arm, dict[qid, QuestionScore|None]], x, y) -> Comparison`. It keeps the qids where both arms are not `None`, and computes the deltas of top-1 per question. It settles the mean with `analysis.settled`, and uses `analysis.bootstrap_ci(deltas, seed=SEED)` (which already settles its bounds) and `analysis.verdict(mean, ci, threshold=THRESHOLD)`. The result is `Comparison(x, y, n, dropped, mean, ci_low, ci_high, verdict)`.
  - `determinism(first: list[ArmResult], repeat: list[ArmResult]) -> dict[arm, int]`: how many qids' top-1 artefact changed.
  - `cost_per_1000(arm, results, rates) -> float`. The rates are a module constant dict with the spec §8 values and their date. S3 adds the ranker's per-request price, 0 on the free plan, labelled as such.
- `report.py`: `render(run: dict) -> str`. It renders exactly what spec §5.3–§5.7 lists:
  - **The verdicts:** for the three comparisons, the verdict text is exactly `difference` or `inconclusive at 300 questions`, using n when fewer survive.
  - **No correction:** the line about running three comparisons with no correction.
  - **Per arm:** the descriptive table, with the latency caveat.
  - **By type:** the breakdown, with "too small to read" for releases.
  - **The appendix:** the exploratory pairs, with no interval.
  - **The caveats:** determinism and the pairs dropped.
- `python -m app.retrieval <subcommand>`, where each subcommand is a thin wrapper over the functions above:
  - `write-questions`, `spot-check`, `freeze`
  - `embed --deployment …`
  - `build-index`
  - `run-arms --repeat-first 30`
  - `report <run_id>`

  `run-arms` writes `eval/reports/retrieval-<run_id>.json`.

- [ ] **Step 1: Write the failing tests**
  - **`test_collapse_keeps_first_appearance`**
  - **`test_target_outside_the_list_scores_zero`** (Review Focus 4)
  - **`test_lenient_accepts_the_merge_commit_both_ways`**
  - **`test_margin_is_within_arm_and_none_on_a_miss`**
  - **`test_errored_pairs_are_dropped_and_counted`** (Review Focus 2, the scoring half): 2 errored qids on S3 give `dropped == 2` and `n == 298`.
  - **`test_rule_at_five_points`:**

    | Mean | CI | Verdict |
    |---|---|---|
    | −0.05 | (−0.09, −0.01) | `difference` |
    | 0.049 | (0.01, 0.09) | `inconclusive` |
    | 0.20 | (−0.01, 0.4) | `inconclusive` |
    | 0.05 | (0.0, 0.1) | `inconclusive` |
  - **`test_verdict_default_threshold_unchanged`** (in `test_analysis.py`): plan 3's 0.10 behaviour is unchanged.
  - **`test_determinism_counts_changed_top1`**
  - **`test_render_states_no_correction_and_small_releases`:** the rendered text contains the exact no-correction sentence and `too small to read`.
- [ ] **Step 2: Run them, and see them fail.**
- [ ] **Step 3: Implement it.** Then write `eval/retrieval/README.md` with the runbook commands from this plan's runbook, placeholders only.
- [ ] **Step 4: Run all the eval tests and expect PASS.**
- [ ] **Step 5: Commit**

`git commit -m "feat(eval): score the arms, decide the three comparisons at five points, and render the report"`

---

### Task 7: Terraform: the embedding deployments in bootstrap, and the `infra/search` stack

**Files:**
- **Modify in `infra/bootstrap`:**
  - `openai.tf`: two `azurerm_cognitive_deployment`s
  - `state.tf`: the container `tfstate-search`
  - `roles.tf`: the owner's `Storage Blob Data Contributor` on it, which makes 8 role assignments
  - `versions.tf`: `Microsoft.Search` in `resource_providers_to_register`, which makes 8 providers. It is already registered in the subscription, so this only records it.
  - `outputs.tf`: the two deployment names
  - the tests in `tests/openai.tftest.hcl` and `tests/state.tftest.hcl`
- **Create `infra/search`:**
  - `versions.tf`, `backend.tf` (azurerm, container `tfstate-search`, key `search.tfstate`), `variables.tf` (`subscription_id`, `location = "australiaeast"`, `owner_object_id`), `main.tf` and `outputs.tf` (`endpoint`)
  - `tests/search.tftest.hcl`
  - `README.md`
- **Modify `tests/infra/test_terraform_static.py`:** 8 providers and 8 role assignments, plus new guards on the search stack.
- **Docs:** update every statement in `README.md`, `docs/architecture.md` and `infra/bootstrap/README.md` that counts role assignments or providers, or lists the deployments. Find them with grep.

**Interfaces:**
- **Produces, used by the runbook:**
  - bootstrap outputs `embedding_small_deployment` and `embedding_large_deployment`
  - the search stack's output `endpoint`, which is `https://<name>.search.windows.net`

- [ ] **Step 1: Write the failing tests**
  - **Bootstrap `openai.tftest.hcl`:**
    - **`embedding_deployments`:** both use GlobalStandard, capacity 350, `NoAutoUpgrade`, version `1` and the spec's names.
    - **`chat_deployment_unchanged`** (Review Focus 5): the chat deployment's name, model, version, capacity 300 and type are as before.
  - **Bootstrap `state.tftest.hcl`:** the `tfstate-search` container is private.
  - **The search stack's `search.tftest.hcl`:**
    - the service is `basic`, with 1 replica and 1 partition
    - `local_authentication_enabled == false` and the semantic search SKU is `free`
    - the resource group is `rg-releaselens-search`
    - exactly 2 role assignments, to `owner_object_id`, with the two role names, scoped to the service
  - **Static, in `test_terraform_static.py`:**
    - **`test_bootstrap_registers_exactly_the_eight_providers`** and **`test_bootstrap_has_the_eight_role_assignments_all_in_roles_tf`**, renamed from seven
    - **`test_search_stack_keeps_keys_off`:** a regex finds `local_authentication_enabled = false`, and no `api_key` or `primary_key` output
    - **`test_search_stack_is_not_in_the_app_group`:** no reference to `rg-releaselens"` without `-search`
- [ ] **Step 2: Run them, and see them fail**

Run, in WSL, as `infra/bootstrap/README.md` describes, with `TF_DATA_DIR=$HOME/tfdata/<stack>` and `-backend=false`: `terraform fmt -check`, `terraform validate` and `terraform test`, for both stacks. Then `python -m pytest tests/infra -q`. Check the azurerm 5.x argument names (`local_authentication_enabled`, `semantic_search_sku`, `replica_count`, `partition_count`) against `terraform providers schema -json` first, and report any rename. For the owner's tfvars, use a clean copy as before.

- [ ] **Step 3: Implement it.** Keep the bootstrap's existing patterns: comments that state why, and `prevent_destroy` where the stack uses it. The search stack has no `prevent_destroy`, because it is meant to be destroyed.
- [ ] **Step 4: Run all of the above and expect PASS.** Run `python scripts/check_workflows.py .github/workflows` too.
- [ ] **Step 5: Commit**

`git commit -m "feat(infra): embedding deployments in bootstrap, and a keyless, short-lived AI Search stack"`

---

## Runbook (after Tasks 1–7 are merged through a pull request)

**The rules for every step:**
- Every **Ask first** step waits for the owner's yes.
- Azure calls run as the owner. Check `az account show` first, and stop if a work account is
  signed in.
- WSL must be kept running by an open WSL window while Docker is in use.
- Postgres is the restored `releaselens_eval`.

1. **The bootstrap change. Ask first.**
   - Plan it in WSL with the live data dir (`$HOME/tfdata/bootstrap-live`). The plan must show
     two deployments and a container being added, one role assignment, and nothing else changed.
   - Apply it.
   - Confirm with `az cognitiveservices account deployment list` that both deployments report
     `Succeeded`.
2. **Export the corpus.** Free.
   - `dotnet run --project src/ReleaseLens.Worker -- export-corpus eval/retrieval-data`.
   - Check that `chunks.jsonl` has 41,825 lines.
3. **The questions. Ask first.** About $1 on Anthropic.
   - Run `write-questions`, then `spot-check`, which writes the sheet for the owner.
   - The owner marks the 30.
   - If more than 3 are not fine, regenerate (another Ask first).
   - Run `freeze`, commit `eval/retrieval/questions.jsonl` and its manifest, and merge through a
     pull request. **Ask first.**
4. **Embed the corpus. Ask first.** About $1.55 on Azure.
   - Run `embed` for each deployment.
   - Record `tokens_billed` for each model.
5. **The measurement session. Ask first.** About $1–2.
   1. Check that `rg-releaselens-search` does not exist.
   2. Apply `infra/search` in WSL.
   3. Run `build-index`, and check the index's document count is 41,825.
   4. Run the Worker `retrieve` for `hybrid` and `bge-exact`.
   5. Run `run-arms --repeat-first 30`.
   6. Destroy `infra/search`, and confirm the group is gone.
6. **Publish. Ask first.**
   - Run `report <run_id>`, then add a dated section to `eval/baseline.md`, and to the README's
     "Evaluation" section the three verdicts, worded exactly as rendered, with date and sample
     size.
   - Push through a pull request.
   - Then update the tracker: project 2 done. **Ask first.**
