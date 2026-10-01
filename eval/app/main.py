from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

# Python verifies TLS against certifi's bundle, not the operating system's trust store,
# so behind a TLS-inspecting corporate proxy every call to api.anthropic.com fails with
# CERTIFICATE_VERIFY_FAILED while the .NET side of this same repository succeeds — it
# already uses the OS store. truststore closes that gap rather than the usual workaround
# of disabling verification, which would trade a broken harness for an insecure one.
# No-op on machines that do not intercept TLS, so it is safe to leave on everywhere.
import truststore

truststore.inject_into_ssl()

from .golden import load_golden  # noqa: E402 — must follow inject_into_ssl()
from .models import RunReport, RunRequest
from .runner import run_eval

app = FastAPI(
    title="ReleaseLens Eval",
    description="Evaluation harness. Runs locally against a ReleaseLens API. Never deployed.",
    version="1.0.0",
)

REPORTS = Path(__file__).resolve().parent.parent / "reports"
REPORTS.mkdir(exist_ok=True)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/golden")
def golden() -> dict[str, object]:
    queries = load_golden()
    by_category: dict[str, int] = {}
    for query in queries:
        by_category[query.category] = by_category.get(query.category, 0) + 1
    return {"count": len(queries), "by_category": by_category}


@app.post("/eval/run", response_model=RunReport)
async def start_run(request: RunRequest) -> RunReport:
    report = await run_eval(request)
    (REPORTS / f"{report.run_id}.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


@app.get("/eval/runs/{run_id}")
def get_run(run_id: str) -> dict[str, object]:
    """The stored report, exactly as it was written.

    Deliberately not validated against RunReport. The report's shape changes as the study
    does, and a report written under an earlier shape is still the record of a run that
    happened and was paid for; validating it against today's model would turn it into a 500.
    """
    path = REPORTS / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No run {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))
