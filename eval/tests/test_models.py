"""The shape of a study, checked before anything is sent.

A sweep costs real money, so a request whose study cannot be carried out as written is
rejected at the door rather than discovered to be broken after it has been paid for.
"""

import pytest
from pydantic import ValidationError

from app.models import Arm, RunRequest


def _arm(name: str, provider: str) -> Arm:
    return Arm(name=name, base_url=f"http://arm-{name.lower()}.invalid", expected_provider=provider)


_ARMS = [_arm("A", "anthropic"), _arm("Z", "azure-openai"), _arm("O", "openai")]


def _study(**overrides) -> RunRequest:
    return RunRequest(**{"api_key": "rl_test", "arms": _ARMS, **overrides})


def test_a_well_formed_study_is_accepted():
    """The control for the test below: each of its requests differs from this one in one field."""
    request = _study(passes=3, comparisons=[("Z", "O"), ("Z", "A")])

    assert [arm.name for arm in request.arms] == ["A", "Z", "O"]
    assert request.passes == 3
    assert request.comparisons == [("Z", "O"), ("Z", "A")]


@pytest.mark.parametrize(
    ("overrides", "complaint"),
    [
        ({"arms": [*_ARMS, _arm("Z", "openai")]}, "duplicate arm name 'Z'"),
        ({"comparisons": [("Z", "X")]}, "unknown arm 'X'"),
        ({"comparisons": [("Z", "Z")]}, "compares arm 'Z' with itself"),
        ({"arms": []}, "arms"),
        ({"passes": 0}, "passes"),
    ],
    ids=["duplicate-arm", "unknown-arm", "arm-against-itself", "no-arms", "no-passes"],
)
def test_run_request_rejects_a_malformed_study(overrides, complaint):
    with pytest.raises(ValidationError) as rejected:
        _study(**overrides)

    # The message names what is wrong, so the operator can fix the request without reading code.
    assert complaint in str(rejected.value)


def test_run_request_has_no_single_api_url():
    """Every arm carries its own URL. A request-wide one would be a second, silent default."""
    assert "api_base_url" not in RunRequest.model_fields
