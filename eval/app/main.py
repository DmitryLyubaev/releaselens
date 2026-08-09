from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .golden import load_golden
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


@app.get("/eval/runs/{run_id}", response_model=RunReport)
def get_run(run_id: str) -> RunReport:
    path = REPORTS / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No run {run_id}")
    return RunReport(**json.loads(path.read_text(encoding="utf-8")))
