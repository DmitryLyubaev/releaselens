"""The gateway's usage metric: per-caller totals, the comparison with the clients' own (B5), and the query.

The metric is `llm-emit-token-metric`'s, in namespace `releaselens-gateway`, with the caller's
`oid` as the `Caller` dimension. An `oid` is never printed or written: `totals` turns each into the
label the owner supplied in `GATEWAY_CALLER_LABELS`, or into `other`, and nothing is dropped.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping

import httpx

from .checks import CheckResult

SCOPE = "https://api.applicationinsights.io/.default"
QUERY_URL = "https://api.applicationinsights.io/v1/apps/{app_id}/query"
NAMESPACE = "releaselens-gateway"
METRIC_NAME = "Total Tokens"
LABELS_VARIABLE = "GATEWAY_CALLER_LABELS"
OTHER = "other"

_SINCE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class MetricQueryError(Exception):
    """The query failed; the message holds the status only, never a URL, a token or a body."""


class LabelsError(Exception):
    """`GATEWAY_CALLER_LABELS` is missing or not a JSON object of strings; the message never echoes it."""


def labels_from_env(env: Mapping[str, str]) -> dict[str, str]:
    """`oid` -> label, from the owner's shell. Nothing here reads or writes a file."""
    raw = env.get(LABELS_VARIABLE)
    if not raw:
        raise LabelsError(f"{LABELS_VARIABLE} is not set: it holds the oid-to-label map as JSON, in your shell only")
    try:
        labels = json.loads(raw)
    except ValueError:
        raise LabelsError(f"{LABELS_VARIABLE} is not valid JSON") from None
    if not isinstance(labels, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()):
        raise LabelsError(f"{LABELS_VARIABLE} must be a JSON object mapping each oid to a label, both strings")
    return labels


def totals(rows: list[dict], labels: Mapping[str, str]) -> dict[str, int]:
    """Total tokens per label. An `oid` that has no label is counted under `other`, never dropped."""
    summed: dict[str, float] = defaultdict(float)
    for row in rows:
        summed[labels.get(row.get("Caller", ""), OTHER)] += row["total"]
    return {label: round(value) for label, value in summed.items()}


def matches(client: dict[str, int], metric: dict[str, int], tolerance: float = 0.02) -> CheckResult:
    """B5: every caller is on both sides, and each total is within `tolerance` of the client's."""
    if not client and not metric:
        return CheckResult("B5", False, "no caller on either side: nothing was recorded or nothing was measured")
    problems, within = [], []
    for caller in sorted(client.keys() | metric.keys()):
        if caller not in client:
            problems.append(f"{caller} is in the metric but not in the clients' records")
        elif caller not in metric:
            problems.append(f"{caller} is in the clients' records but not in the metric")
        else:
            recorded, measured = client[caller], metric[caller]
            if abs(measured - recorded) <= tolerance * recorded:
                within.append(f"{caller} {recorded} vs {measured}")
            else:
                shown = "no tokens recorded" if recorded == 0 else f"{abs(measured - recorded) / recorded:.1%} apart"
                problems.append(f"{caller}: clients {recorded}, metric {measured} ({shown})")
    if problems:
        return CheckResult("B5", False, "; ".join(problems))
    return CheckResult("B5", True, f"within {tolerance:.0%}: " + "; ".join(within))


def check_since(since: str) -> None:
    if _SINCE.fullmatch(since) is None:
        raise ValueError("since must be a UTC time like 2026-10-09T01:30:00Z")


def _kql(since: str) -> str:
    return (
        "customMetrics\n"
        f"| where timestamp >= datetime({since})\n"
        f'| where name == "{METRIC_NAME}"\n'
        f'| where tostring(customDimensions["_MS.MetricNamespace"]) == "{NAMESPACE}"\n'
        '| extend Caller = tostring(customDimensions["Caller"])\n'
        "| summarize total = sum(valueSum) by Caller"
    )


def query(app_id: str, token: str, since: str, http: httpx.Client) -> list[dict]:
    """One POST to the Application Insights query API: tokens per `Caller` since `since` (UTC, to the second)."""
    check_since(since)      # it goes into the KQL text, so only this exact shape gets there
    response = http.post(QUERY_URL.format(app_id=app_id), headers={"Authorization": f"Bearer {token}"},
                         json={"query": _kql(since), "timespan": f"{since}/P3D"})
    if response.status_code != 200:
        raise MetricQueryError(f"the query API answered {response.status_code}")
    try:
        table = response.json()["tables"][0]
        names = [column["name"] for column in table["columns"]]
        return [dict(zip(names, row, strict=True)) for row in table["rows"]]
    except (ValueError, KeyError, IndexError, TypeError):
        raise MetricQueryError("the query API's answer has no result table") from None
