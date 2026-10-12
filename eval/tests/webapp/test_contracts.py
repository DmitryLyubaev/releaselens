from dataclasses import FrozenInstanceError
import pytest
from app.webapp.contracts import Health, Observation, CheckResult, ContractRefused

STAMP = "a" * 40 + ".12.1.stage"
NOW = "2026-10-12T00:00:00Z"


def test_observations_are_immutable_and_serialize_only_allowlisted_data():
    source = {"ask_succeeded": True}
    observation = Observation("W1", {"ask": 200}, source, {"gateway_calls": 1}, {"started_at": NOW})
    source["ask_succeeded"] = False
    assert observation.matches["ask_succeeded"] is True
    with pytest.raises(TypeError):
        observation.matches["ask_succeeded"] = False
    result = CheckResult("W1", "fail", "live", (observation,), "a" * 40, NOW)
    assert result.to_dict()["observations"][0]["statuses"] == {"ask": 200}


@pytest.mark.parametrize("kwargs", [
    {"check": "unexpected"}, {"statuses": {"host": 200}}, {"statuses": {"ask": "secret"}},
    {"statuses": {"ask": True}}, {"statuses": {"ask": 999}},
    {"matches": {"answer": True}}, {"matches": {"ask_succeeded": "yes"}},
    {"counts": {"gateway_calls": -1}}, {"counts": {"gateway_calls": True}},
    {"counts": {"gateway_calls": float("nan")}}, {"counts": {"gateway_calls": float("inf")}},
    {"times": {"started_at": "secret"}}, {"times": {"started_at": "2026-10-12T00:00:00+10:00"}},
])
def test_non_schema_or_nonfinite_values_cannot_reach_public_evidence(kwargs):
    values = dict(check="W1", statuses={}, matches={}, counts={}, times={}) | kwargs
    with pytest.raises(ContractRefused, match="invalid_contract"):
        Observation(**values)


@pytest.mark.parametrize("field,value", [("verdict", "maybe"), ("evidence", "assumed"), ("commit", "host.example.test"), ("measured_at", "yesterday")])
def test_result_requires_fixed_verdict_and_evidence(field, value):
    values = dict(check="W1", verdict="not run", evidence="none", observations=(), commit="a" * 40, measured_at=NOW)
    values[field] = value
    with pytest.raises(ContractRefused):
        CheckResult(**values)
