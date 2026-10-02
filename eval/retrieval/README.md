# Retrieval benchmark: how to run it

This folder will hold the frozen question set, `questions.jsonl`, and its manifest. The design
and the decision rule are fixed in advance, in
[the spec](../../docs/superpowers/specs/2026-10-02-azure-ai-search-benchmark-design.md). This page
says how to run each step, and holds no results.

Every command below runs in PowerShell from `eval/`, with the eval virtual environment active,
unless it says otherwise. That includes the Worker's commands, which name its project as
`../src/ReleaseLens.Worker`. The Terraform commands run in WSL, from the stack's own folder.
Values in `<angle brackets>` are placeholders: fill them in, in your own window, and never commit
them.

## Before every step

- **Ask first.** Each paid step waits for the owner's yes.
- **The account.** Run `az account show`, and stop if a work account is signed in. Azure is called
  as the owner, through the Azure CLI, with no key.
- **TLS inspection.** `AzureCliCredential` gets each token by running `az`. Behind a
  TLS-inspecting proxy, `az` needs a CA bundle that includes the proxy's root, set in the same
  window before any command that calls Azure:

  ```
  $env:REQUESTS_CA_BUNDLE = "<path-to-ca-bundle-with-the-proxy-root.pem>"
  ```

  `python -m app.retrieval` handles its own HTTPS: it puts the operating system's trust store in
  place before any client is built.
- **Claude.** `write-questions` needs `ANTHROPIC_API_KEY` set in the window.
- **The database.** Postgres is the restored `releaselens_eval`, and WSL must be kept running by
  an open WSL window while Docker is in use. The Worker reads its database from
  `RELEASELENS_DB`, so steps 2 and 5, which run the Worker, need it set in that window to
  `releaselens_eval`. A window whose `.env` points at the working database would export, or
  search, the wrong corpus:

  ```
  $env:RELEASELENS_DB = "Host=127.0.0.1;Port=5433;Database=releaselens_eval;Username=releaselens;Password=<dev password>"
  ```

  `run-arms` refuses Worker output with any hit that is not a chunk of the exported corpus, or
  that gives a chunk another artefact.

## 1. The embedding deployments (Ask first)

Apply the bootstrap change from WSL, set up as the bootstrap README's
[Terraform runs in WSL](../../infra/bootstrap/README.md#terraform-runs-in-wsl) says, with the
bootstrap stack's git-ignored `terraform.tfvars` in place. In WSL, from `infra/bootstrap`:

```bash
terraform plan -out=tfplan
```

Read the plan. It must end `Plan: 4 to add, 0 to change, 0 to destroy.`: the two embedding
deployments, the `tfstate-search` container, and the owner's role assignment on that container.
Nothing on the chat deployment or the account changes. If it shows anything else, stop. Then:

```bash
terraform apply tfplan
terraform output -raw azure_openai_base_url
```

The output is the whole `--base-url` for steps 4 and 6, ending in `/openai/v1/`. The tenant for
`--tenant` is printed by:

```
az account show --query tenantId -o tsv
```

Keep both in your own window only. Then confirm that both deployments report `Succeeded`. The
first command prints the account's name, which the second takes as `<account>`:

```
az cognitiveservices account list --resource-group rg-releaselens-bootstrap --query "[].name" -o tsv
az cognitiveservices account deployment list --name <account> --resource-group rg-releaselens-bootstrap --output table
```

## 2. Export the corpus (free)

With `RELEASELENS_DB` set to `releaselens_eval` (see [Before every step](#before-every-step)):

```
dotnet run --project ../src/ReleaseLens.Worker -- export-corpus retrieval-data
```

Check that `retrieval-data/chunks.jsonl` has 41,825 lines. `retrieval-data/` is git-ignored: it
holds the export, the vectors, the spot-check sheets and the Worker's outputs.

## 3. The questions (Ask first: about $1 on Anthropic)

```
python -m app.retrieval write-questions
```

Each accepted question is appended, with its draft, to
`retrieval-data/questions.checkpoint.jsonl` as it passes. Every paid call to Claude is appended
to `retrieval-data/questions.spend.jsonl` as it returns, accepted or not. If the run fails partway,
run the same command again: it skips the questions already saved, and pays only for the rest.
The question it failed on is written again from its first draft, and the spend record counts
both attempts, so the totals are what was really spent.

Then write the spot-check sheet:

```
python -m app.retrieval spot-check --round 0
```

The sheet also records what writing this generation cost: its calls and tokens. The owner opens
`retrieval-data/spot-check-round-0.json`, and sets each of the 30 `mark` fields to `fine`,
`ambiguous` or `wrong`. Then freeze:

```
python -m app.retrieval freeze --sheet retrieval-data/spot-check-round-0.json
```

`freeze` refuses a sheet with more than 3 that are not fine. If that happens, the set is
regenerated, which is another Ask first:
1. Change the prompt or the checks.
2. Move the old checkpoint and its spend record aside together, into a folder of their own, and
   keep both: a checkpoint written under another prompt is refused, and the old generation's
   spend is part of what the set cost. Keep the round's marked sheet where it is.
3. Run `write-questions` again.
4. Run `spot-check --round 1`, which draws a fresh 30.
5. Freeze with every round's sheet, from round 0, oldest first:
   `freeze --sheet retrieval-data/spot-check-round-0.json --sheet retrieval-data/spot-check-round-1.json`

   Each round's spend goes into the manifest's `spot_check`, and their sum into
   `spend_all_rounds`, so the generations that were thrown away are counted too. `freeze`
   refuses a round left out, and an earlier round's sheet in `retrieval-data/` that was not
   given.

`freeze` writes `retrieval/questions.jsonl` and `retrieval/questions.manifest.json`. The manifest
holds:
- the seed, the model and the prompts (`PROMPT` and `REWRITE`)
- the generation and freezing dates
- the rejection counts, and the calls and tokens the set took
- each spot-check round with its marks and its generation's spend
- each question's `why_unique`
- the file's SHA-256

Commit both files, and merge them through a pull request (Ask first). Every later step reads only
that file, and refuses it if it has changed since freezing.

## 4. Embed the corpus (Ask first: about $1.55 on Azure)

```
python -m app.retrieval embed --deployment releaselens-embed-small --base-url <base-url> --tenant <tenant-id>
python -m app.retrieval embed --deployment releaselens-embed-large --base-url <base-url> --tenant <tenant-id>
```

Each prints its `tokens_billed`, which `run-arms` reads back from the deployment's
`retrieval-data/<deployment>.progress.json` into the run, so the write-up states what embedding
the corpus cost. A run that fails partway (a throttle past its retries, or a dropped connection)
is resumed by running the same command again, and pays for no batch already saved.

## 5. The Worker's arms (free)

These run locally and cost nothing, so they come before the search service is applied: a
failure here is fixed while nothing bills by the hour. They need `RELEASELENS_DB` set to
`releaselens_eval` in this window (see [Before every step](#before-every-step)), even if steps 2
to 4 ran in another session.

Run each mode **twice**, into separate files. The second run of each mode is the determinism
repeat for E1 and S1.

```
dotnet run --project ../src/ReleaseLens.Worker -- retrieve hybrid retrieval/questions.jsonl retrieval-data/worker-hybrid.jsonl
dotnet run --project ../src/ReleaseLens.Worker -- retrieve hybrid retrieval/questions.jsonl retrieval-data/worker-hybrid-repeat.jsonl
dotnet run --project ../src/ReleaseLens.Worker -- retrieve bge-exact retrieval/questions.jsonl retrieval-data/worker-bge.jsonl
dotnet run --project ../src/ReleaseLens.Worker -- retrieve bge-exact retrieval/questions.jsonl retrieval-data/worker-bge-repeat.jsonl
```

## 6. The measurement session (Ask first: about $1–2)

1. Check that no search group is left from an earlier session. This must print `false`:

   ```
   az group exists --name rg-releaselens-search
   ```

2. Apply `infra/search` from WSL, as its README's
   [Apply and destroy](../../infra/search/README.md#apply-and-destroy) says:
   - Its inputs go in a git-ignored `infra/search/terraform.tfvars`, in your own window.
   - `terraform init -backend-config=storage_account_name=<state storage account>` sets up its
     state. In `infra/bootstrap`, `terraform output -raw tfstate_storage_account` prints the
     account's name.
   - Plan, read the plan, and apply it. `terraform output -raw endpoint` then prints the
     `--endpoint` for the next steps.
3. Build the index:

   ```
   python -m app.retrieval build-index --endpoint <endpoint> --tenant <tenant-id>
   ```

   It prints the documents uploaded, and then the index's document count, which must be 41,825.
   Any document the service refuses fails the upload, naming it. The index counts a document a
   moment after it is uploaded, so `build-index` reads the count for up to about a minute, and
   fails if it never reaches the corpus. A new role assignment can take a few minutes to take
   effect: if `build-index` gets a `403` straight after the apply, wait a few minutes and run it
   again.
4. Run the network arms and the repeat:

   ```
   python -m app.retrieval run-arms --repeat-first 30 --base-url <base-url> --endpoint <endpoint> --tenant <tenant-id> --worker-hybrid retrieval-data/worker-hybrid.jsonl --worker-hybrid-repeat retrieval-data/worker-hybrid-repeat.jsonl --worker-bge retrieval-data/worker-bge.jsonl --worker-bge-repeat retrieval-data/worker-bge-repeat.jsonl
   ```

   Before it pays for anything, it checks:
   - the frozen file
   - the four Worker files, down to each hit being a chunk of the exported corpus
   - the saved vectors, and each deployment's record that embedding the corpus finished
   - a token for each scope, so a sign-in failure costs nothing
   - that the index at `--endpoint` holds all 41,825 documents, so an empty or wrong index is
     refused rather than scored as misses

   It then runs E2, E3, S2 and S3 over all 300 questions, and repeats the first 30 on each of
   them. S2 and S3 search with E2's vectors on both passes. For E1 and S1 it compares the first
   30 questions of each pair of Worker files.

   The run is saved to `reports/retrieval-<run_id>.json` after each arm, and the run id is
   printed. If an arm fails as a whole, the run stops there, and is saved with that failure. It
   is not analysed. A search arm that gets the same error 10 times in a row stops as such a
   failure, since that is a setup error and not one question's.

   **A run has no resume.** A failed run is run again from the start, with a new run id, and
   pays again for everything it had done. The scarce part is the semantic ranker's free
   allowance of 1,000 requests a month. A failure after S3's first pass has already spent about
   300 of them, and the rerun spends about 330 more, so a month has room for about two failed
   attempts and a successful one. Running out of the allowance part-way through S3 returns the
   same error for every remaining question, so it also stops the run: wait for the next month
   rather than running again.

5. **Whatever happened above, even if a step failed,** destroy `infra/search` from WSL, as its
   README says, then confirm that the same `az group exists` check prints `false`. A forgotten
   Basic service costs about US$3.19 a day.

## 7. Publish (Ask first)

```
python -m app.retrieval report <run_id>
```

This prints the write-up, in UTF-8, exactly as it is to be published. Then:
1. Add a dated section to `eval/baseline.md`.
2. Add the three verdicts to the README's "Evaluation" section, worded exactly as rendered, with
   the date and the sample size.
3. Push through a pull request.
4. Then update the tracker: project 2 is done (Ask first).
