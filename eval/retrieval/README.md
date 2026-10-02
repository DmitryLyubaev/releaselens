# Retrieval benchmark: how to run it

This folder will hold the frozen question set, `questions.jsonl`, and its manifest. The design
and the decision rule are fixed in advance, in
[the spec](../../docs/superpowers/specs/2026-10-02-azure-ai-search-benchmark-design.md). This page
says how to run each step, and holds no results.

Every command below that starts with `python -m app.retrieval` runs from `eval/`, with the eval
virtual environment active. The Worker and Terraform commands run from the repository root.
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
  an open WSL window while Docker is in use.

## 1. The embedding deployments (Ask first)

Apply the bootstrap change from WSL. Its plan must add the two deployments and the
`tfstate-search` container, with one role assignment and nothing else changed. Then confirm that
both deployments report `Succeeded`:

```
az cognitiveservices account deployment list --name <account> --resource-group <resource-group> --output table
```

## 2. Export the corpus (free)

```
dotnet run --project src/ReleaseLens.Worker -- export-corpus eval/retrieval-data
```

Check that `eval/retrieval-data/chunks.jsonl` has 41,825 lines. `eval/retrieval-data/` is
git-ignored: it holds the export, the vectors, the spot-check sheets and the Worker's outputs.

## 3. The questions (Ask first: about $1 on Anthropic)

```
python -m app.retrieval write-questions
```

Each accepted question is appended, with its draft, to
`retrieval-data/questions.checkpoint.jsonl` as it passes. If the run fails partway, run the same
command again: it skips the questions already saved, and pays only for the rest.

Then write the spot-check sheet:

```
python -m app.retrieval spot-check --round 0
```

The owner opens `retrieval-data/spot-check-round-0.json`, and sets each of the 30 `mark` fields to
`fine`, `ambiguous` or `wrong`. Then freeze:

```
python -m app.retrieval freeze --sheet retrieval-data/spot-check-round-0.json
```

`freeze` refuses a sheet with more than 3 that are not fine. If that happens, the set is
regenerated, which is another Ask first:
1. Change the prompt or the checks.
2. Move the old checkpoint aside: a checkpoint written under another prompt is refused.
3. Run `write-questions` again.
4. Run `spot-check --round 1`, which draws a fresh 30.
5. Freeze with every round's sheet, oldest first:
   `freeze --sheet retrieval-data/spot-check-round-0.json --sheet retrieval-data/spot-check-round-1.json`

`freeze` writes `retrieval/questions.jsonl` and `retrieval/questions.manifest.json`. The manifest
holds:
- the seed, the model and the prompts (`PROMPT` and `REWRITE`)
- the generation and freezing dates
- the rejection counts
- each spot-check round with its marks
- each question's `why_unique`
- the file's SHA-256

Commit both files, and merge them through a pull request (Ask first). Every later step reads only
that file, and refuses it if it has changed since freezing.

## 4. Embed the corpus (Ask first: about $1.55 on Azure)

```
python -m app.retrieval embed --deployment releaselens-embed-small --base-url https://<account>.openai.azure.com/openai/v1/ --tenant <tenant-id>
python -m app.retrieval embed --deployment releaselens-embed-large --base-url https://<account>.openai.azure.com/openai/v1/ --tenant <tenant-id>
```

Record the `tokens_billed` each one prints. A run that fails partway (a throttle past its
retries, or a dropped connection) is resumed by running the same command again, and pays for no
batch already saved.

## 5. The measurement session (Ask first: about $1–2)

1. Check that no search group is left from an earlier session. This must print `false`:

   ```
   az group exists --name rg-releaselens-search
   ```

2. Apply `infra/search` from WSL.
3. Build the index:

   ```
   python -m app.retrieval build-index --endpoint https://<search-service>.search.windows.net --tenant <tenant-id>
   ```

   It prints the documents uploaded, which must be 41,825. Any document the service refuses fails
   the upload, naming it, so every document counted was accepted.
4. Run the Worker's two arms, each mode **twice**, into separate files. The second run of each
   mode is the determinism repeat for E1 and S1; both modes are local, so it costs nothing.

   ```
   dotnet run --project src/ReleaseLens.Worker -- retrieve hybrid eval/retrieval/questions.jsonl eval/retrieval-data/worker-hybrid.jsonl
   dotnet run --project src/ReleaseLens.Worker -- retrieve hybrid eval/retrieval/questions.jsonl eval/retrieval-data/worker-hybrid-repeat.jsonl
   dotnet run --project src/ReleaseLens.Worker -- retrieve bge-exact eval/retrieval/questions.jsonl eval/retrieval-data/worker-bge.jsonl
   dotnet run --project src/ReleaseLens.Worker -- retrieve bge-exact eval/retrieval/questions.jsonl eval/retrieval-data/worker-bge-repeat.jsonl
   ```

5. Run the network arms and the repeat:

   ```
   python -m app.retrieval run-arms --repeat-first 30 --base-url https://<account>.openai.azure.com/openai/v1/ --endpoint https://<search-service>.search.windows.net --tenant <tenant-id> --worker-hybrid retrieval-data/worker-hybrid.jsonl --worker-hybrid-repeat retrieval-data/worker-hybrid-repeat.jsonl --worker-bge retrieval-data/worker-bge.jsonl --worker-bge-repeat retrieval-data/worker-bge-repeat.jsonl
   ```

   It checks the frozen file, the four Worker files and the saved vectors before it pays for
   anything. It then runs E2, E3, S2 and S3 over all 300 questions, and repeats the first 30 on
   each of them. S2 and S3 search with E2's vectors on both passes. For E1 and S1 it compares the
   first 30 questions of each pair of Worker files.

   The run is saved to `reports/retrieval-<run_id>.json` after each arm, and the run id is
   printed. If an arm fails as a whole, for example when a token cannot be had, the run stops
   there and is saved with that failure. It is not analysed: run it again with a new run id.

6. Destroy `infra/search` from WSL, then confirm that the same `az group exists` check prints
   `false`.

## 6. Publish (Ask first)

```
python -m app.retrieval report <run_id>
```

This prints the write-up, in UTF-8, exactly as it is to be published. Then:
1. Add a dated section to `eval/baseline.md`.
2. Add the three verdicts to the README's "Evaluation" section, worded exactly as rendered, with
   the date and the sample size.
3. Push through a pull request.
4. Then update the tracker: project 2 is done (Ask first).
