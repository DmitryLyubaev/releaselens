"""B1, B2, B4 and the smoke check, on fixtures: no network, no real sleep."""

from app.gateway.checks import Reply, access, day_budget, minute_budget, rev2_url, smoke

MODEL = {"x-ms-region": "Australia East"}


def _ok(**headers) -> Reply:
    return Reply(200, {**MODEL, **headers}, prompt_tokens=300, completion_tokens=20)


def _gateway_429(retry_after: str | None = "12") -> Reply:
    return Reply(429, {} if retry_after is None else {"Retry-After": retry_after})


class _Feed:
    """A `send` that answers from a list, in order, and counts how often it was asked."""

    def __init__(self, replies: list[Reply]) -> None:
        self.replies = list(replies)
        self.sent = 0

    def __call__(self) -> Reply:
        self.sent += 1
        return self.replies.pop(0)


class _Sleeps:
    def __init__(self) -> None:
        self.seconds: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


# --- B1 ---------------------------------------------------------------------------------------

def test_b1_passes_on_a_429_with_retry_after_and_no_model_call():
    send = _Feed([_ok(), _ok(), _gateway_429()])
    result = minute_budget(send)
    assert (result.check, result.passed) == ("B1", True)
    assert send.sent == 3


def test_b1_fails_when_the_429_came_from_a_model():
    result = minute_budget(_Feed([_ok(), Reply(429, {"Retry-After": "5", **MODEL})]))
    assert result.passed is False and "model" in result.detail


def test_b1_fails_when_the_429_has_no_retry_after():
    result = minute_budget(_Feed([_ok(), _gateway_429(retry_after=None)]))
    assert result.passed is False and "Retry-After" in result.detail


def test_b1_fails_when_nothing_is_refused_within_the_cap():
    send = _Feed([_ok() for _ in range(5)])
    result = minute_budget(send, max_requests=5)
    assert result.passed is False and send.sent == 5


def test_b1_stops_and_fails_on_any_other_refusal():
    for status in (401, 403, 503, None):
        send = _Feed([_ok(), Reply(status), _ok()])
        result = minute_budget(send)
        assert result.passed is False and send.sent == 2, status


# --- B2 ---------------------------------------------------------------------------------------

def _big(tokens: int) -> Reply:
    return Reply(200, MODEL, prompt_tokens=tokens - 20, completion_tokens=20)


def test_b2_keeps_going_past_minute_budget_429s_waiting_their_retry_after():
    send = _Feed([_big(30000), _gateway_429("7"), _big(20000), _gateway_429("11"), Reply(403)])
    sleeps = _Sleeps()
    result = day_budget(send, sleep=sleeps)
    assert (result.check, result.passed) == ("B2", True)
    assert sleeps.seconds == [7, 11] and send.sent == 5


def test_b2_waits_a_minute_when_a_429_does_not_say_how_long():
    sleeps = _Sleeps()
    day_budget(_Feed([_gateway_429(None), Reply(403)]), sleep=sleeps)
    assert sleeps.seconds == [60]


def test_b2_passes_a_403_that_arrives_once_45000_tokens_are_recorded():
    result = day_budget(_Feed([_big(45000), Reply(403)]), sleep=_Sleeps())
    assert result.passed is True and "45000 tokens recorded today before the 403" in result.detail


def test_b2_fails_a_403_below_45000_and_states_the_figure():
    result = day_budget(_Feed([_big(44999), Reply(403)]), sleep=_Sleeps())
    assert result.passed is False
    assert "44999 tokens recorded today before the 403" in result.detail and "45000" in result.detail


def test_b2_fails_a_403_that_is_the_first_answer_with_nothing_recorded():
    result = day_budget(_Feed([Reply(403)]), sleep=_Sleeps())
    assert result.passed is False and "0 tokens recorded today before the 403" in result.detail


def test_b2_counts_the_tokens_recorded_earlier_in_the_session():
    assert day_budget(_Feed([_big(1000), Reply(403)]), sleep=_Sleeps(), recorded_tokens=44000).passed is True
    below = day_budget(_Feed([_big(1000), Reply(403)]), sleep=_Sleeps(), recorded_tokens=43999)
    assert below.passed is False and "44999 tokens recorded today before the 403" in below.detail


def test_b2_counts_only_the_tokens_of_200_answers():
    refused = Reply(429, {"Retry-After": "1"}, prompt_tokens=99999)
    result = day_budget(_Feed([_big(40000), refused, Reply(403)]), sleep=_Sleeps())
    assert result.passed is False and "40000 tokens" in result.detail


def test_b2_fails_when_the_cap_is_reached_without_a_403():
    send = _Feed([_ok() for _ in range(4)])
    result = day_budget(send, sleep=_Sleeps(), max_requests=4)
    assert result.passed is False and send.sent == 4


def test_b2_fails_on_an_unexpected_status():
    for status in (401, 500, None):
        assert day_budget(_Feed([_ok(), Reply(status)]), sleep=_Sleeps()).passed is False, status


# --- B4 ---------------------------------------------------------------------------------------

def test_b4_passes_when_both_calls_get_401():
    result = access(lambda: Reply(401), lambda: Reply(401))
    assert (result.check, result.passed) == ("B4", True)


def test_b4_fails_when_either_call_gets_anything_else():
    assert access(lambda: Reply(401), lambda: Reply(200)).passed is False
    assert access(lambda: Reply(200), lambda: Reply(401)).passed is False
    assert access(lambda: Reply(403), lambda: Reply(401)).passed is False
    assert access(lambda: Reply(None), lambda: Reply(401)).passed is False


def test_b4_says_which_call_failed_without_naming_a_token():
    result = access(lambda: Reply(401), lambda: Reply(200))
    assert "wrong-audience" in result.detail and "200" in result.detail


# --- smoke ------------------------------------------------------------------------------------

def _labelled(**extra) -> Reply:
    return Reply(200, {"x-ms-region": "Southeast Asia", "x-releaselens-backend": "secondary",
                       "content-type": "application/json", **extra}, '{"choices": []}')


def test_smoke_passes_when_only_labels_come_back():
    results = smoke(_labelled, _labelled, _labelled)
    assert [r.check for r in results] == ["smoke-direct", "smoke-gateway", "smoke-rev2"]
    assert all(r.passed for r in results)


def test_smoke_reports_the_status_both_signals_and_the_header_names():
    (direct, *_) = smoke(_labelled, _labelled, _labelled)
    for expected in ("200", "Southeast Asia", "secondary", "content-type", "x-ms-region"):
        assert expected in direct.detail


def test_smoke_says_when_a_signal_is_absent():
    (direct, *_) = smoke(lambda: Reply(200, {"content-type": "application/json"}), _labelled, _labelled)
    assert direct.passed and "x-ms-region absent" in direct.detail


def test_smoke_fails_when_the_gateway_body_contains_a_hostname_and_does_not_repeat_it():
    host = "aoai-releaselens-sea-a1b2c3.openai.azure.com"
    leaky = lambda: Reply(200, {"x-ms-region": "Southeast Asia"}, f'{{"error": "{host}"}}')
    direct, gateway, rev2 = smoke(_labelled, leaky, _labelled)
    assert (direct.passed, gateway.passed, rev2.passed) == (True, False, True)
    assert host not in gateway.detail and "azure.com" not in gateway.detail


def test_smoke_fails_when_a_header_value_contains_any_of_the_three_hostnames():
    for host in ("a.openai.azure.com", "apim-x.azure-api.net", "x.blob.core.windows.net"):
        result = smoke(lambda: Reply(200, {"location": f"https://{host}/x"}, "{}"), _labelled, _labelled)[0]
        assert result.passed is False and host not in result.detail, host


def test_smoke_fails_on_a_non_200():
    assert smoke(_labelled, lambda: Reply(404, {}, "{}"), _labelled)[1].passed is False


def test_the_revision_2_url_is_exactly_as_the_spec_says():
    assert rev2_url("https://gateway.example.com/openai/v1/") == (
        "https://gateway.example.com/openai/v1;rev=2/chat/completions")
    assert rev2_url("https://gateway.example.com/openai/v1") == (
        "https://gateway.example.com/openai/v1;rev=2/chat/completions")
