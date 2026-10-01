# Azure OpenAI keyless: the evaluation, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the pre-registered three-arm study in spec §8, and publish its result under the
spec's decision rule. The arms are Anthropic, Azure OpenAI through Entra, and OpenAI through a
key. To get there, the harness must change as spec §7 requires, and F7 and F8 must be fixed.

**Architecture:**
- **The harness posts to several arms.** It takes an ordered list of arms, one local API
  process per arm, in place of one API URL. Its loop runs pass, then query, then arm, rotating
  the arm order each pass. It tags each outcome with its arm and pass.
- **Analysis is pure, deterministic Python.** It pairs outcomes by query and pass, averages the
  passes per query, bootstraps a 95% confidence interval over queries, and applies the
  pre-registered decision rule.
- **One C# change.** The runtime gains OpenAI's `gpt-4.1-mini` rate. Without it, arm O refuses
  to start.
- **The money is spent in an owner-gated runbook:** the dry run, the real run and the
  write-up.

**Tech Stack:** Python 3.14 (FastAPI, httpx, pydantic, anthropic SDK, pytest) in `eval/`; C# .NET 10 for one pricing row.

**Spec:** `docs/superpowers/specs/2026-09-24-azure-openai-keyless-design.md`
- approved 2026-09-24, and amended since
- this plan implements §7's "Evaluation harness" list, §8 and the fixes for F7 and F8
- plans 1 and 2 are merged: the runtime, then the infrastructure and delivery

## Global Constraints

- **The study design is pre-registered (spec §8).** Implement it as written and change none of
  it.
- **Arms (§8):**

  | Arm | Provider (`expected_provider`) | Model | Auth |
  |---|---|---|---|
  | A | `anthropic` | Claude Sonnet 5 | key |
  | Z | `azure-openai` | gpt-4.1-mini 2025-04-14 (Global Standard) | the owner's Entra identity (`AzureCliCredential`) |
  | O | `openai` | gpt-4.1-mini | key |

  Each arm is a separate local API process on its own port, with a single-entry
  `Chat:Providers`. All three use the same restored database. `/query` gains no provider input.
- **Queries (§8):** `per_category=2`, which gives gq-001, 002, 014, 015, 022, 023, 029, 030, 036
  and 037. Eight are answerable; gq-036 and gq-037 are unanswerable.
- **Passes (§8):** three. The arm order is A,Z,O, then Z,O,A, then O,A,Z. That is the arm list
  rotated left by the pass index.
- **Judge (§8):** Claude Sonnet 5, blinded to the arm.
- **Comparisons (§8):** Z against O tests the claim. Z against A is reported as well, but it
  compares two different models.
- **Unit of analysis (§8):** the query.
  - For each arm, a query's passes are averaged first, then arms are paired by query.
  - The 95% confidence interval is a bootstrap that resamples queries with their passes kept
    together.
- **Metric scopes (§8):** each metric is scored only over the queries it applies to.
  - **groundedness:** the (query, pass) pairs where both arms were judged, over all 10 queries
  - **citation recall and citation precision:** the 8 answerable queries
  - **`must_contain` pass rate:** 6 queries (gq-001, 002, 022, 023, 029, 030). Queries without
    `must_contain` are left out, not passed.
  - **unanswerable accuracy:** 2 queries, reported per arm, descriptively only
- **Decision rule (§8):**
  - It applies only to the four 0–1 quality metrics above.
  - A difference is declared only when the mean paired delta is ≤ −0.10 or ≥ +0.10 **and** its
    95% confidence interval excludes zero.
  - Anything else is reported as **"inconclusive at k queries × 3 passes"**.
  - Per-query deltas are published alongside the means.
- **Reported descriptively per arm, with no significance claim (§8):** latency p50 and p95 in
  ms, and cost per query in USD.
- **Recorded, not explained away (§8):**
  - Azure content-filter events
  - the response's `model` string from the final iteration, for each query
  - that Z authenticates as the owner, not as the managed identity
- **Rates, all in USD per 1M tokens:**
  - **OpenAI `gpt-4.1-mini`, Standard tier:** input 0.40, cached input 0.10, output 1.60. Read
    on 2026-10-02 from https://developers.openai.com/api/docs/pricing; the page shows no date.
  - **Judge, Claude Sonnet 5 from 2026-09-01:** input 3.00, output 15.00. These are
    `ModelPricing`'s `claude-sonnet-5` rates after 2026-08-31.
- **OpenAI counts cached tokens inside `prompt_tokens`.** OpenAI's cookbook (read 2026-10-02,
  https://developers.openai.com/cookbook/examples/prompt_caching101) says `cached_tokens` shows
  "how many of the prompt tokens were a cache hit". Its example has `prompt_tokens` 1136 with
  `cached_tokens` 1024. This confirms the premise of F4. `OpenAiWireFormat` already subtracts
  cached tokens from `prompt_tokens`.
- **Spending (§8):**
  - the dry run costs about $0.55
  - the real run costs about $5, and could be half or double that
  - **both need the owner's approval first**
  - nothing in Tasks 1–6 spends money or reaches a real provider
- **Tests use no network.** They replace the HTTP client and the Anthropic client, as
  `eval/tests/` already does.
- **No fabrication.**
  - A figure is published only with its date and sample size.
  - Anything unproven is labelled unproven.
  - "Inconclusive" is written wherever the rule says so (spec §10).
- **Keys never enter a file.** `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` come from the owner's
  git-ignored `.env`. The issued ReleaseLens API key is passed to the harness by environment
  variable, never written down. Nothing prints any of them.
- **Commits:**
  - the repo-local identity `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`
  - never `git config --global`, and never `--author`
  - every message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  - work happens on branch `feat/azure-openai-evaluation`. `main` requires the
    `build-and-test` and `terraform-plan` checks, so everything lands through a pull request.

## Review Focus

1. **An arm that fails some queries mid-run, through a 5xx, a connection refused or a degraded
   answer,** is recorded as an error outcome for that arm. It must not be silently scored as a
   quality loss. The pairs it breaks drop out, and the report states how many pairs each
   comparison used. (Task 3 `test_a_failed_arm_query_is_an_error_not_a_score`; Task 5
   `test_pairs_with_an_errored_side_are_dropped_and_counted`)
2. **An answer from the wrong provider, or from none,** is an error with a message naming the
   arm, the expected provider and what answered. A filtered answer from the arm's own provider is
   *not* that error: it is recorded as filtered. (Task 3
   `test_wrong_provider_rule`)
3. **A judgement that cannot be parsed on one side of a pair** removes that pair from
   groundedness only. It stays in the citation metrics. (Task 5
   `test_unscored_groundedness_drops_the_pair_from_groundedness_only`)
4. **A request with duplicate arm names, or a comparison naming an unknown arm or the same arm
   twice,** is rejected before any query is sent, so no money is spent on a malformed study.
   (Task 2 `test_run_request_rejects_a_malformed_study`)
5. **Degenerate data:** zero variance, a single query, or no usable pairs at all. Each gives a
   deterministic verdict or `"no data"`, never a crash or a NaN in the report. (Task 5
   `test_degenerate_inputs`)

---

### Task 1: OpenAI's `gpt-4.1-mini` rate, and the verified §11 rows

**Files:**
- Modify: `src/ReleaseLens.Llm/Providers/ModelPricing.cs` (`ResolveOpenAi`)
- Test: `tests/ReleaseLens.Llm.Tests/ModelPricingTests.cs`
- Modify: `docs/superpowers/specs/2026-09-24-azure-openai-keyless-design.md` (§11 table)

**Interfaces:** Produces the identity `new PricingIdentity("openai", "gpt-4.1-mini")`, priced.
Arm O runs with `OpenAi:Model=gpt-4.1-mini`, and `EnsurePriced` must accept it at startup.

- [ ] **Step 1: Write the failing tests**

```csharp
[Fact]
public void OpenAiGpt41Mini_UsesOpenAisPublishedRates()
{
    var identity = new PricingIdentity("openai", "gpt-4.1-mini");
    var oneMillionEach = new TokenUsage(InputTokens: 1_000_000, OutputTokens: 1_000_000,
        CacheReadInputTokens: 1_000_000, CacheCreationInputTokens: 0);
    Assert.Equal(2.10m, ModelPricing.CostUsd(identity, oneMillionEach, AfterIntroductoryPricing)); // 0.40 + 1.60 + 0.10
}

[Fact]
public void EnsurePriced_AcceptsTheOpenAiArmsIdentity()
    => ModelPricing.EnsurePriced([new PricingIdentity("openai", "gpt-4.1-mini")], AfterIntroductoryPricing);
```

Use the file's existing `TokenUsage` constructor shape and its `AfterIntroductoryPricing`
constant.

- [ ] **Step 2: Run them, and see them fail**

Run: `dotnet test tests/ReleaseLens.Llm.Tests --filter "FullyQualifiedName~ModelPricingTests"`
Expected: both FAIL, with "No rate for pricing identity openai gpt-4.1-mini".

- [ ] **Step 3: Add the rate**

Add `"gpt-4.1-mini" => new Rates(0.40m, 0.10m, 0m, 1.60m)` to `ResolveOpenAi`. Give it a comment
citing the OpenAI pricing page, the Standard tier, read 2026-10-02. This model has its cached
rate read from the page, so the existing upper-bound comment does not apply to it.

- [ ] **Step 4: Run all of `ReleaseLens.Llm.Tests`, and see them pass**

Run: `dotnet test tests/ReleaseLens.Llm.Tests`. Expected: all pass, with 0 warnings.

- [ ] **Step 5: Mark the spec's §11 rows as settled**

Strike through each item and add a dated note, in the table's existing style:
- **"OpenAI's own price for `gpt-4.1-mini`":** read 2026-10-02, with the three rates and the URL.
- **"Whether OpenAI's `prompt_tokens` includes cached tokens":** yes, per OpenAI's cookbook,
  read 2026-10-02, quoting its "how many of the prompt tokens were a cache hit".
- **"What Azure returns in the response `model` field":** `gpt-4.1-mini-2025-04-14`, in both
  smoke tests on 2026-10-01. `metadata.model` comes from the response's `model` field
  (`OpenAiWireFormat`).

- [ ] **Step 6: Commit**

`git commit -m "feat(pricing): OpenAI gpt-4.1-mini at its published rates, and settle three §11 rows"`

---

### Task 2: The study's request and outcome shape, and CI running the eval tests

**Files:**
- Modify: `eval/app/models.py`, `eval/app/main.py`
- Test: `eval/tests/test_models.py` (new), `eval/tests/test_golden.py`
- Modify: `.github/workflows/ci.yml` (`build-and-test`)

**Interfaces:**
- **Produces (in `eval/app/models.py`):**
  - `Arm(name: str, base_url: str, expected_provider: str)`
  - `RunRequest` without `api_base_url`, with:
    - `arms: list[Arm]` (at least one)
    - `passes: int = 1` (at least 1)
    - `comparisons: list[tuple[str, str]] = []`, where a pair `(x, y)` means the delta x − y
    - every existing field kept as it is
  - `QueryOutcome` gains these fields:
    - `arm: str`
    - `pass_index: int`
    - `provider: str | None = None`
    - `providers: list[str] = []`
    - `model: str | None = None`
    - `filtered_stage: str | None = None`
    - `judge_cost_usd: float = 0.0`
- **Produces (in `eval/app/main.py`):** `GET /eval/runs/{run_id}` returns the stored JSON as it
  is, without validating it against `RunReport`. Reports written before this change still open.
  Task 5 replaces `RunReport`'s aggregate fields.

- [ ] **Step 1: Write the failing tests**
  - **`test_models.py::test_run_request_rejects_a_malformed_study`** (Review Focus 4). Each of
    these raises `pydantic.ValidationError`:
    - two arms named `"Z"`
    - `comparisons=[("Z", "X")]`, which names an unknown arm
    - `comparisons=[("Z", "Z")]`
    - `arms=[]`
    - `passes=0`
  - **`test_models.py::test_run_request_has_no_single_api_url`:** `"api_base_url" not in
    RunRequest.model_fields`.
  - **`test_golden.py::test_the_preregistered_selection`:** `[q.id for q in
    _stratify(load_golden(), 2)]` equals the ten IDs in Global Constraints, in that order. The
    IDs whose `must_contain` is non-empty are exactly gq-001, 002, 022, 023, 029 and 030. The
    categories of gq-036 and gq-037 are `unanswerable`.

- [ ] **Step 2: Run them, and see them fail**

Run (from `eval/`): `.venv/Scripts/python.exe -m pytest tests/test_models.py tests/test_golden.py -q`
Expected: the model tests FAIL. The selection test may already PASS: it guards the
pre-registration, so record its result either way.

- [ ] **Step 3: Implement the models**
  - Validate with a `model_validator(mode="after")` on `RunRequest`.
  - Change `get_run` to return `json.loads(...)` as a plain dict, with
    `response_model` removed.

- [ ] **Step 4: CI runs the eval tests**

In `ci.yml` `build-and-test`:
- set the existing `setup-python` step's `python-version` to `'3.14'`, which matches
  `eval/Dockerfile`
- add a step that runs `pip install -r eval/requirements.txt`, then
  `python -m pytest -q` with `working-directory: eval`

Keep the SHA pin. Running `python -m pytest tests/infra -q` locally on 3.14 must still pass.

- [ ] **Step 5: Run everything, and see it pass**

Run: `.venv/Scripts/python.exe -m pytest -q` from `eval/`, and `python -m pytest tests/infra -q`
from the root. Expected: all pass. Fix the runner test fixtures that built `RunRequest` with
`api_base_url`, using a single `Arm`.

- [ ] **Step 6: Commit**

`git commit -m "feat(eval): a run takes arms, passes and comparisons; CI runs the eval tests"`

---

### Task 3: The runner, covering rotation, tagging, the wrong-provider rule and the dry-run estimate

**Files:**
- Modify: `eval/app/runner.py`
- Test: `eval/tests/test_runner.py`

**Interfaces:**
- **Consumes:** `Arm`, `RunRequest` and `QueryOutcome` from Task 2.
- **Produces (in `runner.py`):**
  - `arm_order(arms: list[Arm], pass_index: int) -> list[Arm]`: the list rotated left by
    `pass_index % len(arms)`.
  - `provider_error(arm: Arm, metadata: dict) -> str | None`:
    - **returns `None`** when `metadata["providers"] == [arm.expected_provider]`, or when
      `metadata.get("filtered")` names `arm.expected_provider`
    - **otherwise returns** `"arm {name} expected {expected}; answered by {providers or 'none'}"`
  - `run_eval(request)`: loops `for pass_index in range(passes)`, then for each query, then for
    each arm in `arm_order(...)`. It posts to `{arm.base_url}/query`, and every outcome carries
    its arm, pass index, `provider`, `providers`, `model` and `filtered_stage`.

- [ ] **Step 1: Write the failing tests**

The fake client answers per base URL, from a dict keyed by URL.

- **`test_arm_order_rotates_by_pass`:**
  - with arms A, Z and O, passes 0, 1 and 2 give `AZO`, `ZOA` and `OAZ`
  - pass 3 gives `AZO` again
  - one arm always gives itself
- **`test_requests_follow_pass_then_query_then_arm`:** with 2 queries, 3 arms and 2 passes, the
  sequence of `(base_url, question)` sent is exactly the nested order.
- **`test_each_outcome_is_tagged`:** `arm`, `pass_index`, `provider`, `providers` and `model`
  come from the reply's `metadata`.
- **`test_wrong_provider_rule`** (Review Focus 2), with `expected_provider="azure-openai"`:
  - `providers == ["anthropic"]` is an error naming "Z", "azure-openai" and "anthropic"
  - `providers == []` is an error that says "none"
  - `["azure-openai", "openai"]` is an error
  - `filtered={"stage": "prompt", "provider": "azure-openai"}` with `providers == []` is not an
    error, and gives `filtered_stage == "prompt"`
- **`test_a_failed_arm_query_is_an_error_not_a_score`** (Review Focus 1): a 503 from one arm on
  one query gives an outcome with `error` set, groundedness `None`, and the arm and pass still
  tagged. The other arms' outcomes for that query are unaffected.
- **`test_dry_run_prices_the_run_it_precedes`:**
  - set-up: a dry run with `per_category=2` and `passes=3`, the judge off, and each fake arm
    costing $0.01 a query
  - it sends one query per category per arm, in a single pass
  - `estimated_cost_usd_before_run == 0.01 * 10 * 3 * 3` (10 queries, 3 passes, 3 arms)

- [ ] **Step 2: Run them, and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_runner.py -q`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement**
  - **Wrong provider:** an outcome whose `provider_error` is not `None` keeps the reply's
    metrics fields as zeros and `None`, the same as a failed request. It carries the message in
    `error`.
  - **The dry run** runs one pass over `_stratify(selection, 1)`, where `selection` is what the
    real request would select.
  - **Its estimate** is the sum over arms of (that arm's mean dry-run cost per query) ×
    `len(selection)` × `passes`. When `judge` is on, add the mean judge estimate per dry-run
    answer × `len(selection)` × `passes` × `len(arms)`, priced with the existing
    `estimate_cost` for now. Task 4 changes how the judge is priced.

- [ ] **Step 4: Run the eval tests, and see them pass**

Run: `.venv/Scripts/python.exe -m pytest -q`. Expected: all pass.

- [ ] **Step 5: Commit**

`git commit -m "feat(eval): rotate arms by pass, tag outcomes, and reject wrong-provider answers"`

---

### Task 4: The judge, covering F7, F8 and blinding

**Files:**
- Modify: `eval/app/judge.py`, `eval/app/runner.py` (the call site)
- Test: `eval/tests/test_judge.py`, `eval/tests/test_runner.py`

**Interfaces:**
- **Produces (in `judge.py`):**
  - the constants `JUDGE_INPUT_USD_PER_MTOK = 3.00`, `JUDGE_OUTPUT_USD_PER_MTOK = 15.00` and
    `JUDGE_OUTPUT_ALLOWANCE_TOKENS = 512`
  - `@dataclass(frozen=True) class Judgement: score: float; reason: str; cost_usd: float`
  - `async def score(question, answer, evidence) -> Judgement`: its cost comes from
    `response.usage.input_tokens` and `output_tokens` at the two rates. An unparseable reply
    keeps `score=-1.0` and is still priced.
  - `async def estimate_cost_usd(items) -> float`:
    - for each item, `count_tokens` input × the input rate
    - plus `JUDGE_OUTPUT_ALLOWANCE_TOKENS` × the output rate
- **Removed from `runner.py`:** `_JUDGE_INPUT_USD_PER_MTOK` (F8).
- **Added to `RunReport`:** `judge_cost_usd: float = 0.0`. Task 5 keeps it in the replaced
  report.

- [ ] **Step 1: Write the failing tests**
  - **`test_a_judgement_is_priced_with_output_tokens`:** the fake `create` returns usage
    input=10,000 and output=200, and `Judgement.cost_usd == 10_000*3/1e6 + 200*15/1e6`.
  - **`test_the_estimate_includes_the_output_allowance`:** for one item of N counted tokens, the
    estimate is `N*3/1e6 + 512*15/1e6`.
  - **`test_the_module_claims_no_batch_api`** (F7): `"Batch" not in judge_module.__doc__`.
  - **`test_the_judge_is_blinded_to_the_arm`** (in `test_runner.py`):
    - set-up: a run over arms whose fake replies have metadata `provider` values
      `"azure-openai"` and `"openai"`, and whose answers name no provider
    - no prompt the judge sends contains `"azure-openai"`, `"openai"`, `"anthropic"`, an arm name
      as a whole word, or a model string
  - **`test_judge_cost_lands_on_the_outcome`:** each judged outcome has `judge_cost_usd` set,
    and the report's `judge_cost_usd` is their sum.

  Make the fakes return `usage` objects, using `SimpleNamespace` as now.

- [ ] **Step 2: Run them, and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_judge.py tests/test_runner.py -q`. Expected:
the new tests FAIL.

- [ ] **Step 3: Implement**
  - Delete the Batch API sentence from the module docstring. Do not replace it with a promise.
  - Cite the rates' source and date in a comment.
  - The runner judges outcomes in their run order. It never passes the arm, the provider or the
    model to the judge.
  - The dry-run estimate from Task 3 now prices the judge with `estimate_cost_usd`.

- [ ] **Step 4: Run the eval tests, and see them pass**

Run: `.venv/Scripts/python.exe -m pytest -q`. Expected: all pass.

- [ ] **Step 5: Commit**

`git commit -m "fix(eval): price the judge at Sonnet 5's current rates with output tokens, and drop the Batch API claim (F7, F8)"`

---

### Task 5: The analysis, covering paired deltas, the bootstrap, the decision rule and arm summaries

**Files:**
- Create: `eval/app/analysis.py`
- Modify: `eval/app/models.py` (`RunReport`), `eval/app/runner.py` (building the report)
- Test: `eval/tests/test_analysis.py` (new), `eval/tests/test_runner.py`

**Interfaces:**
- **Consumes:** tagged `QueryOutcome`s from Task 3, and `GoldenQuery`.
- **Produces (in `analysis.py`):**
  - **Constants:** `QUALITY_METRICS = ("groundedness", "citation_recall", "citation_precision",
    "must_contain")`, `DIFFERENCE_THRESHOLD = 0.10`, `BOOTSTRAP_RESAMPLES = 10_000` and
    `BOOTSTRAP_SEED = 20260924`
  - `metric_value(outcome: QueryOutcome, query: GoldenQuery, metric: str) -> float | None`.
    `None` means the metric does not apply to this query, or this outcome has no value for it.
    - `groundedness` applies to every query, and is `None` when unscored
    - `citation_recall` and `citation_precision` apply when `category != "unanswerable"`
    - `must_contain` applies when `query.must_contain` is non-empty, scoring 1.0 or 0.0
    - an outcome with `error` set is `None` for every metric
  - `paired_query_deltas(outcomes, queries: dict[str, GoldenQuery], x: str, y: str, metric: str)
    -> dict[str, float]`:
    - pairs x's and y's outcomes by `(query_id, pass_index)`
    - keeps the pairs where both values are not `None`
    - per query, returns the mean of (x − y) over the pairs it kept
    - leaves out a query with no kept pair
  - `bootstrap_ci(deltas: list[float], resamples: int = BOOTSTRAP_RESAMPLES, seed: int =
    BOOTSTRAP_SEED) -> tuple[float, float]`:
    - resamples the per-query deltas with replacement, using `random.Random(seed)`
    - returns the 2.5th and 97.5th percentiles of the resampled means, by nearest rank
  - `verdict(mean_delta: float, ci: tuple[float, float]) -> str`:
    - `"difference"` when `abs(mean_delta) >= DIFFERENCE_THRESHOLD` and (`ci[0] > 0` or
      `ci[1] < 0`)
    - otherwise `"inconclusive"`
  - `class Comparison(BaseModel)`, with these fields:
    - `metric`, `x`, `y`
    - `k: int` (queries) and `pairs: int`
    - `passes: int`
    - `mean_delta`, `ci_low`, `ci_high` (each `float | None`)
    - `verdict: str`, one of `"difference"`, `"inconclusive"` and `"no data"`
    - `per_query_delta: dict[str, float]`
  - `compare(outcomes, queries, x, y, passes) -> list[Comparison]`: one comparison per quality
    metric. With k = 0, the verdict is `"no data"` and the three numbers are `None`.
  - `class ArmSummary(BaseModel)` and `summarise(outcomes, queries, arm: Arm) -> ArmSummary`,
    each metric scoped as in Global Constraints. Its fields:
    - `outcome_count`, `error_count`, `filtered_count`
    - a mean and its count for each quality metric
    - `unanswerable_handled` and `unanswerable_total`
    - `p50_latency_ms` and `p95_latency_ms`, from `latency_percentiles`
    - `mean_cost_usd_per_query` and `total_cost_usd`, over outcomes without errors
    - `models_seen: list[str]`
- **`RunReport` (replaced):**
  - `run_id`, `started_at`, `passes`
  - `query_ids: list[str]`: the real run's selection, even on a dry run
  - `arms: list[Arm]`
  - `arm_summaries: list[ArmSummary]`
  - `comparisons: list[Comparison]`
  - `judge_cost_usd`, `total_cost_usd`, `estimated_cost_usd_before_run`
  - `outcomes`

  The old aggregate fields go. Their vacuous 1.0 for unanswerable queries was the flaw that
  `eval/baseline.md` corrects by hand.

- [ ] **Step 1: Write the failing tests**

Use hand-built outcomes.

- **`test_metric_scopes`:**
  - an unanswerable query gives `None` for both citation metrics and a value for groundedness
  - a query without `must_contain` gives `None` for `must_contain`
  - an errored outcome gives `None` for every metric
- **`test_passes_are_averaged_before_pairing`:**
  - set-up: x scores 1.0, 1.0 and 0.0 over three passes of one query; y scores 0.5 each time
  - the delta for that query is `2/3 - 0.5`
- **`test_pairs_with_an_errored_side_are_dropped_and_counted`** (Review Focus 1): with one
  errored pass, `pairs` is 2 for that query and k is unchanged.
- **`test_unscored_groundedness_drops_the_pair_from_groundedness_only`** (Review Focus 3).
- **`test_bootstrap_is_reproducible_and_brackets_the_mean`:**
  - the same seed gives the same interval twice
  - `ci_low <= mean <= ci_high`
  - constant deltas d give `(d, d)`
- **`test_verdict_rule`:**

  | Mean delta | CI | Verdict |
  |---|---|---|
  | −0.10 | (−0.2, −0.01) | `difference` |
  | 0.09 | (0.01, 0.2) | `inconclusive` |
  | 0.25 | (−0.05, 0.5) | `inconclusive` |
  | 0.10 | (0.0, 0.2) | `inconclusive`, because a CI touching zero does not exclude it |
- **`test_degenerate_inputs`** (Review Focus 5):
  - k = 1 gives a CI of `(d, d)` and a deterministic verdict
  - k = 0 gives `"no data"` with `None` numbers
  - `RunReport.model_dump_json()` contains no `NaN`
- **`test_report_carries_summaries_and_the_requested_comparisons`** (in `test_runner.py`):
  - set-up: `comparisons=[("Z", "O"), ("Z", "A")]`
  - the report gives 8 comparisons, 4 metrics × 2, in that order, and one summary per arm in
    arm order

- [ ] **Step 2: Run them, and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_analysis.py tests/test_runner.py -q`.
Expected: FAIL, because `app.analysis` does not exist yet.

- [ ] **Step 3: Implement `analysis.py`, and build the new `RunReport` in `run_eval`**

Use the standard library only, with no numpy. The percentile index for fraction p of n sorted
resampled means is `ceil(p * n) - 1`, clamped to `[0, n - 1]`. State it in the docstring.

- [ ] **Step 4: Run the eval tests, and see them pass**

Run: `.venv/Scripts/python.exe -m pytest -q`. Expected: all pass.

- [ ] **Step 5: Commit**

`git commit -m "feat(eval): paired per-query analysis with a bootstrap CI and the pre-registered decision rule"`

---

### Task 6: The study write-up renderer, and the runbook in the docs

**Files:**
- Create: `eval/app/study_report.py`
- Test: `eval/tests/test_study_report.py` (new)
- Modify: `eval/baseline.md`, a short "how to run the three-arm study" section, with no results
- Modify: `README.md` "Evaluation", one sentence pointing to it, with no results

**Interfaces:**
- **Consumes:** the `RunReport` from Task 5.
- **Produces:**
  - `render_markdown(report: dict) -> str`. It takes the stored JSON, so it can render any
    saved run.
  - `python -m app.study_report <run_id>`, which prints the markdown for
    `reports/<run_id>.json`.

- [ ] **Step 1: Write the failing tests**
  - **`test_inconclusive_is_worded_with_its_sample_size`:** a comparison with `k=8`, `passes=3`
    and verdict `inconclusive` renders the text `inconclusive at 8 queries × 3 passes`.
  - **`test_every_per_query_delta_is_published`:** each id in each `per_query_delta` appears in
    the output.
  - **`test_descriptive_figures_carry_no_verdict`:** the latency and cost table has no verdict
    column.
  - **`test_the_header_states_date_sample_and_auth`:** the output contains
    - the run's `started_at` date
    - `10 queries × 3 passes`, from `len(query_ids)` and `passes`
    - the arms table with each arm's expected provider
    - the line "Z authenticates as the owner (Azure CLI), not as the deployed managed identity"
  - **`test_models_seen_and_filter_events_are_listed`:** each arm's `models_seen` and
    `filtered_count` appear.

- [ ] **Step 2: Run them, and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_study_report.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement the renderer**

It produces exactly the published figures and no others. Every number comes from the report,
with nothing computed afresh except the formatting:
- deltas and CIs to 3 decimal places
- cost to 4 decimal places
- latency in whole milliseconds

- [ ] **Step 4: Document how to run the study**

In `eval/baseline.md`, add a section with how to run the study: the arms, ports, settings and
commands from runbook steps E1–E3 below, with placeholders for every key, and no results.

- [ ] **Step 5: Run the eval tests, and see them pass**

Run: `.venv/Scripts/python.exe -m pytest -q`. Expected: all pass.

- [ ] **Step 6: Commit**

`git commit -m "feat(eval): render a study run as the markdown that gets published, and document how to run it"`

---

## Runbook (after Tasks 1–6 are merged through a pull request)

**Which steps ask first.** Every step marked **Ask first** waits for the owner's yes. E4 and E5
spend money. A step that starts a process with the owner's keys is run by the owner, or with the
owner's yes, with the keys loaded from their `.env` and never printed.

- **E1. Restore the corpus,** following `E:\Projects\ReleaseLens-backup\RESTORE.md`.
  1. Start the stack with `docker compose up -d postgres`.
  2. Create the database `releaselens_eval`.
  3. Run `pg_restore --no-owner` from `releaselens-corpus-2026-08-12.dump`. If the restore
     reports that role `releaselens_app` is missing, create it by hand as migration
     `007_app_role.sql` does (§8).
  4. Point `RELEASELENS_DB` at `releaselens_eval`.
  5. Run `dotnet run --project src/ReleaseLens.Worker -- migrate`.
  6. Issue a key, and capture it straight into the shell so it is never shown:
     `$env:EVAL_API_KEY = (dotnet run --project src/ReleaseLens.Worker -- issue-key semantic-kernel eval)`.
     Check that it matches `rl_` followed by hex. It never goes into a file.
  7. Check the row counts against `RESTORE.md`'s table.
- **E2. Start the three arms.** Run `dotnet build -c Release` once. Then run one process per arm
  with `dotnet run --project src/ReleaseLens.Api -c Release --no-build --no-launch-profile
  --urls http://localhost:<port>`, with these settings:
  - **A on port 8081:** `Chat__Providers__0=anthropic`, with `ANTHROPIC_API_KEY`.
  - **Z on port 8082:** `Chat__Providers__0=azure-openai`, `AzureOpenAi__BaseUrl=<the base URL
    from bootstrap's outputs>`, `AzureOpenAi__Deployment=releaselens-chat`,
    `AzureOpenAi__Credential=AzureCli` and `AzureOpenAi__TenantId=<tenant>`.
    - Run `az login` first, as the personal account (RULES.md: stop if a work account is
      signed in).
    - Set `REQUESTS_CA_BUNDLE` for this process only, as the memory note on `az` TLS describes.
  - **O on port 8083:** `Chat__Providers__0=openai`, `OpenAi__Model=gpt-4.1-mini`, with
    `OPENAI_API_KEY`.

  Each process must start. A missing price stops startup, which is what Task 1 fixes. Then
  check `GET /health` on each port.
- **E3. Start the harness.** From `eval/`, run `.venv/Scripts/fastapi run app/main.py --port
  8000`, with `ANTHROPIC_API_KEY` set for the judge's `count_tokens` and scoring.
- **E4. Dry run.** **Ask first (about $0.55, which is spec §8's estimate).**
  - **The request:** `POST /eval/run` with
    - `arms`: A, Z and O at `http://localhost:8081`, `:8082` and `:8083`
    - `per_category=2`, `passes=3`, `judge=true`, `dry_run=true`
    - `comparisons=[["Z","O"],["Z","A"]]`
    - `api_key`: `$EVAL_API_KEY`
  - **Record:**
    - each arm's tokens per query
    - `estimated_cost_usd_before_run`
    - any wrong-provider errors
  - **The capacity check (§4.8):** compare Z's largest query, multiplied by the queries Z runs
    per minute, with 100,000 TPM. Revise the price estimate and report both numbers to the owner
    before E5.
- **E5. The study.** **Ask first (about $5, and up to double).** The same request as E4, with
  `dry_run=false`. Keep the report's `run_id`.
- **E6. Write it up.** **Ask first, before the push.**
  - Run `python -m app.study_report <run_id>`.
  - Add its output to `eval/baseline.md` as a dated section.
  - Replace the README's "Evaluation" lead table with the study's per-arm figures and the Z
    against O verdicts, worded exactly as the renderer gives them, with date and sample size.
  - Keep the 12 August figures as history.
  - Record the filter events and `models_seen` as legitimate differences, as §8 requires.
  - Commit on a branch, open a pull request, and merge once CI is green.
- **E7. Update the tracker.** **Ask first, before the push.** Log the run in
  `E:\Development\portfolio-plan\TRACKER.md`, and mark project 1 done once E6 is merged.

**Not in this plan:** the deliberately filtered prompt that §11 lists for Azure's 400 wire shape.
It needs a prompt the owner chooses and approves. The classifier keeps accepting both shapes
until then.
