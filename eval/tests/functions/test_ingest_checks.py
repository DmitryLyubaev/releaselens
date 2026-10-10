"""I1 to I4 on fixtures, and the REST calls under them: the index by artefact, the blob upload, the queue peek."""

import base64
import json
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest

from app.functions import ingest_checks as ic

ARTEFACT = "issue:42"
BLOB_ENDPOINT = "https://store.example.com/"
QUEUE_ENDPOINT = "https://store.example.com/"
SEARCH = "https://search.example.com"
TOKEN = "fake-token-not-real"


def _keys(artefact: str, n: int) -> list[str]:
    return [ic.chunk_key(artefact, i) for i in range(n)]


def _docs(artefact: str, n: int, text: str = "some text") -> list[dict]:
    return [{"chunk_id": key, "content": f"{text} {i}"} for i, key in enumerate(_keys(artefact, n))]


class World:
    """An index that changes with the fake clock: `schedule(at, artefact, chunks)` sets what it holds from `at` on."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.states: dict[str, list[tuple[float, list[dict]]]] = {}
        self.reads = 0

    def schedule(self, at: float, artefact: str, chunks: list[dict]) -> None:
        self.states.setdefault(artefact, []).append((at, chunks))

    def read(self, artefact: str) -> list[dict]:
        self.reads += 1
        held: list[dict] = []
        for at, chunks in sorted(self.states.get(artefact, []), key=lambda state: state[0]):
            if at <= self.clock.now:
                held = chunks
        return held


# --- names and keys -----------------------------------------------------------------------------

def test_chunk_key_is_the_csharp_rule():
    assert ic.chunk_key("issue:42", 0) == "aXNzdWU6NDI-0"
    assert ic.chunk_key("pull_request:5055", 12).endswith("-12")
    assert "=" not in ic.chunk_key("a", 0) and ":" not in ic.chunk_key("commit:abc.def", 1)


def test_the_blob_name_is_the_session_and_the_base64url_key_and_needs_no_percent_encoding():
    stem = base64.urlsafe_b64encode(b"pull_request:5055").rstrip(b"=").decode()
    name = ic.blob_name("ab12cd34", stem)
    assert name == f"ab12cd34-{stem}.json"
    assert name == httpx.URL(f"https://x.example.com/c/{name}").path.rsplit("/", 1)[1]


def test_the_artefact_is_read_back_from_the_file_stem_and_a_stem_that_is_not_a_key_is_refused():
    stem = base64.urlsafe_b64encode(b"commit:0f3a.b").rstrip(b"=").decode()
    assert ic.artefact_of_stem(stem) == "commit:0f3a.b"
    for bad in ("not base64!", "aXNzdWU6NDI=", "", "AAAA"):
        with pytest.raises(ValueError):
            ic.artefact_of_stem(bad)


def test_a_new_session_id_is_eight_lowercase_hex_characters_and_differs_each_time():
    ids = {ic.new_session_id() for _ in range(20)}
    assert len(ids) == 20 and all(len(i) == 8 and set(i) <= set("0123456789abcdef") for i in ids)


# --- the index, by artefact -----------------------------------------------------------------------

def _index_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_chunks_for_artefact_filters_selects_and_sends_no_top():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"value": [{"chunk_id": "k0", "content": "c0"}]})

    chunks = ic.chunks_for_artefact(_index_client(handler), SEARCH, lambda: TOKEN, "issue:it's")

    assert chunks == [{"chunk_id": "k0", "content": "c0"}]
    request = seen[0]
    body = json.loads(request.content)
    assert body == {"search": "*", "filter": "artefact eq 'issue:it''s'", "select": "chunk_id,content"}
    assert request.method == "POST" and request.url.path == "/indexes/releaselens-chunks/docs/search"
    assert request.url.params["api-version"] == "2026-04-01"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


def test_chunks_for_artefact_follows_the_skip_and_keeps_the_first_filter():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "skip" not in body:
            return httpx.Response(200, json={
                "value": _docs(ARTEFACT, 50),
                "@odata.nextLink": f"{SEARCH}/indexes/releaselens-chunks/docs/search?api-version=2026-04-01",
                "@search.nextPageParameters": {"search": "*", "filter": "other", "skip": 50}})
        assert body["skip"] == 50
        return httpx.Response(200, json={"value": _docs(ARTEFACT, 3, "page two")})

    chunks = ic.chunks_for_artefact(_index_client(handler), SEARCH, lambda: TOKEN, ARTEFACT)

    assert len(chunks) == 53
    assert bodies[1]["filter"] == f"artefact eq '{ARTEFACT}'" and "top" not in bodies[1]


def test_chunks_for_artefact_refuses_a_page_that_does_not_move_forward():
    def handler(request):
        return httpx.Response(200, json={
            "value": [], "@odata.nextLink": f"{SEARCH}/x", "@search.nextPageParameters": {"skip": 0}})

    with pytest.raises(ic.IndexReadError):
        ic.chunks_for_artefact(_index_client(handler), SEARCH, lambda: TOKEN, ARTEFACT)


def test_chunks_for_artefact_raises_on_a_refusal_with_the_status_only():
    with pytest.raises(ic.IndexReadError) as raised:
        ic.chunks_for_artefact(_index_client(lambda r: httpx.Response(403, text="https://secret.example.com")),
                               SEARCH, lambda: TOKEN, ARTEFACT)
    assert str(raised.value) == "the index answered 403" and "secret" not in str(raised.value)


# --- the upload and the poison queue ---------------------------------------------------------------

def test_upload_blob_puts_a_block_blob_to_artefacts_in_with_the_bearer_token_and_never_overwrites():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201)

    ic.upload_blob(_index_client(handler), BLOB_ENDPOINT, lambda: TOKEN, "ab12cd34-xyz.json", b'{"a": 1}')

    request = seen[0]
    assert request.method == "PUT" and request.url.path == "/artefacts-in/ab12cd34-xyz.json"
    assert request.headers["x-ms-blob-type"] == "BlockBlob" and "x-ms-version" in request.headers
    assert request.headers["authorization"] == f"Bearer {TOKEN}" and request.content == b'{"a": 1}'
    assert request.headers["if-none-match"] == "*"


def test_upload_blob_with_overwrite_sends_no_condition():
    seen = []
    ic.upload_blob(_index_client(lambda r: seen.append(r) or httpx.Response(201)), BLOB_ENDPOINT, lambda: TOKEN,
                   "n.json", b"{}", overwrite=True)
    assert "if-none-match" not in seen[0].headers


@pytest.mark.parametrize("status", [409, 412])
def test_upload_blob_says_plainly_that_the_blob_already_exists(status):
    with pytest.raises(ic.UploadError) as raised:
        ic.upload_blob(_index_client(lambda r: httpx.Response(status, text="https://secret.example.com")),
                       BLOB_ENDPOINT, lambda: TOKEN, "n.json", b"{}")
    assert "already exists" in str(raised.value) and "--new-session" in str(raised.value)
    assert "secret" not in str(raised.value)


def test_upload_blob_raises_on_anything_but_201_with_the_status_only():
    with pytest.raises(ic.UploadError) as raised:
        ic.upload_blob(_index_client(lambda r: httpx.Response(403, text="https://secret.example.com")),
                       BLOB_ENDPOINT, lambda: TOKEN, "n.json", b"{}")
    assert str(raised.value) == "the blob upload answered 403"


def _event(blob: str) -> dict:
    return {"topic": "t", "subject": f"/blobServices/default/containers/artefacts-in/blobs/{blob}",
            "eventType": "Microsoft.Storage.BlobCreated",
            "data": {"url": f"https://store.example.com/artefacts-in/{blob}"}}


def _queue_xml(texts: list[str], inserted: str = "Sat, 10 Oct 2026 03:30:15 GMT") -> str:
    messages = "".join(f"<QueueMessage><MessageId>m{i}</MessageId><InsertionTime>{inserted}</InsertionTime>"
                       f"<MessageText>{text}</MessageText></QueueMessage>" for i, text in enumerate(texts))
    return f'<?xml version="1.0" encoding="utf-8"?><QueueMessagesList>{messages}</QueueMessagesList>'


def _queue_handler(texts: list[str], count: int | None = None, seen: list | None = None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if request.url.params.get("comp") == "metadata":
            return httpx.Response(200, headers={"x-ms-approximate-messages-count": str(len(texts) if count is None else count)})
        return httpx.Response(200, text=_queue_xml(texts))

    return handler


def test_peek_poison_only_peeks_and_returns_the_texts_with_their_insertion_times():
    seen = []
    messages = ic.peek_poison(_index_client(_queue_handler(["one", "two"], seen=seen)), QUEUE_ENDPOINT, lambda: TOKEN)

    assert [m.text for m in messages] == ["one", "two"]
    assert messages[0].inserted_at == datetime(2026, 10, 10, 3, 30, 15, tzinfo=timezone.utc).timestamp()
    peek = [r for r in seen if r.url.params.get("peekonly")]
    assert len(peek) == 1
    request = peek[0]
    assert request.method == "GET" and request.url.path == "/ingest-events-poison/messages"
    assert request.url.params["peekonly"] == "true" and request.url.params["numofmessages"] == "32"
    assert request.headers["authorization"] == f"Bearer {TOKEN}" and "x-ms-version" in request.headers
    assert {r.method for r in seen} == {"GET"}


def test_peek_poison_reads_the_approximate_count_first_with_get_queue_metadata():
    seen = []
    ic.peek_poison(_index_client(_queue_handler([], seen=seen)), QUEUE_ENDPOINT, lambda: TOKEN)
    first = seen[0]
    assert first.url.path == "/ingest-events-poison" and first.url.params["comp"] == "metadata"
    assert first.headers["authorization"] == f"Bearer {TOKEN}"


def test_peek_poison_refuses_a_queue_with_more_messages_than_one_peek_returns_and_does_not_peek():
    seen = []
    with pytest.raises(ic.QueueReadError) as raised:
        ic.peek_poison(_index_client(_queue_handler(["x"], count=33, seen=seen)), QUEUE_ENDPOINT, lambda: TOKEN)
    assert "33" in str(raised.value) and "32" in str(raised.value)
    assert not any(r.url.params.get("peekonly") for r in seen)
    ic.peek_poison(_index_client(_queue_handler(["x"], count=32)), QUEUE_ENDPOINT, lambda: TOKEN)


def test_peek_poison_reads_an_empty_queue_and_raises_on_a_refusal():
    assert ic.peek_poison(_index_client(_queue_handler([])), QUEUE_ENDPOINT, lambda: TOKEN) == []
    with pytest.raises(ic.QueueReadError) as raised:
        ic.peek_poison(_index_client(lambda r: httpx.Response(403, text="x")), QUEUE_ENDPOINT, lambda: TOKEN)
    assert str(raised.value) == "the queue answered 403"


def test_a_message_without_an_insertion_time_has_none():
    xml = '<QueueMessagesList><QueueMessage><MessageText>t</MessageText></QueueMessage></QueueMessagesList>'

    def handler(request):
        if request.url.params.get("comp") == "metadata":
            return httpx.Response(200, headers={"x-ms-approximate-messages-count": "1"})
        return httpx.Response(200, text=xml)

    assert ic.peek_poison(_index_client(handler), QUEUE_ENDPOINT, lambda: TOKEN)[0].inserted_at is None


def test_a_poison_message_names_its_blob_whether_plain_or_base64():
    plain = json.dumps(_event("ab12cd34-broken.json"))
    encoded = base64.b64encode(plain.encode()).decode()
    assert ic.blob_of_message(plain) == "ab12cd34-broken.json"
    assert ic.blob_of_message(encoded) == "ab12cd34-broken.json"
    assert ic.blob_of_message(json.dumps([_event("a.json")])) == "a.json"


def test_a_poison_message_that_is_not_an_event_names_no_blob():
    for text in ("garbage", "", base64.b64encode(b"not json").decode(), "{}", '{"subject": 5}',
                 json.dumps({"subject": "/other/path"})):
        assert ic.blob_of_message(text) is None


# --- I1: watched inside the upload, judged from what was watched --------------------------------------

def _new(artefact: str, expected: int, at: float) -> ic.Upload:
    stem = base64.urlsafe_b64encode(artefact.encode()).rstrip(b"=").decode()
    return ic.Upload(kind="new", artefact=artefact, blob=ic.blob_name("ab12cd34", stem), uploaded_at=at,
                     expected_chunks=expected)


def _five(at: float) -> list[ic.Upload]:
    return [_new(f"issue:{n}", n % 3 + 1, at) for n in range(1, 6)]


def test_watch_new_records_when_each_artefact_became_searchable(clock):
    world = World(clock)
    uploads = _five(clock.now)
    for n, upload in enumerate(uploads):
        world.schedule(clock.now + 10 * (n + 1), upload.artefact, _docs(upload.artefact, upload.expected_chunks))

    watched = ic.watch_new(uploads, world.read, clock=clock, sleep=clock.sleep)

    assert all(u.watched for u in watched)
    assert [u.observed_after for u in watched] == [10, 20, 30, 40, 50]
    assert all(u.problem is None for u in watched)
    assert clock.sleeps and all(s > 0 for s in clock.sleeps)


def test_watch_new_watches_to_the_end_of_the_window_and_records_what_was_wrong(clock):
    world = World(clock)
    on_time, late, partial = (_new("issue:1", 2, clock.now), _new("issue:2", 2, clock.now), _new("issue:3", 4, clock.now))
    world.schedule(clock.now + 120, on_time.artefact, _docs(on_time.artefact, 2))
    world.schedule(clock.now + 125, late.artefact, _docs(late.artefact, 2))
    world.schedule(clock.now + 5, partial.artefact, _docs(partial.artefact, 3))

    a, b, c = ic.watch_new([on_time, late, partial], world.read, clock=clock, sleep=clock.sleep)

    assert a.observed_after == 120 and a.problem is None
    assert b.observed_after is None and b.problem and b.watched
    assert c.observed_after is None and "3 of 4" in c.problem


def test_i1_passes_when_every_upload_was_watched_searchable_in_time_and_reports_median_and_maximum(clock):
    uploads = [replace(_new(f"issue:{n}", 1, 0), watched=True, observed_after=10.0 * n) for n in range(1, 6)]
    result = ic.i1_new_searchable(uploads)
    assert (result.check, result.passed) == ("I1", True)
    assert "5 of 5" in result.detail and "median 30 s" in result.detail and "maximum 50 s" in result.detail


def test_i1_passes_at_120_and_fails_when_an_upload_missed_its_window_naming_it():
    ok = replace(_new("issue:1", 2, 0), watched=True, observed_after=120.0)
    missed = replace(_new("issue:2", 2, 0), watched=True, observed_after=None, problem="1 of 2 chunks")
    assert ic.i1_new_searchable([ok]).passed is True
    result = ic.i1_new_searchable([ok, missed])
    assert result.passed is False and "120 s" in result.detail and "issue:2" in result.detail and "1 of 2" in result.detail


def test_i1_fails_a_watched_upload_seen_only_after_the_window():
    late = replace(_new("issue:1", 2, 0), watched=True, observed_after=125.0)
    assert ic.i1_new_searchable([late]).passed is False


def test_i1_never_judges_an_upload_it_did_not_watch_even_when_later_batches_were_watched():
    watched = replace(_new("issue:1", 1, 0), watched=True, observed_after=5.0)
    unwatched = _new("issue:2", 1, 0)
    with pytest.raises(ic.NotWatchedError) as raised:
        ic.i1_new_searchable([watched, unwatched])
    assert "issue:2" in str(raised.value) and "upload" in str(raised.value)


def test_i1_fails_with_no_uploads():
    assert ic.i1_new_searchable([]).passed is False


# --- I2: no typed numbers ---------------------------------------------------------------------------------

OLD = ("9001", "9002", "9003", "9004")
OLD_TEXTS = ["old head", "old middle", "the cut tail", "old end"]


def _changed(at: float = 0, *, old: tuple[str, ...] = OLD, gone: str = "the cut tail") -> ic.Upload:
    return ic.Upload(kind="changed", artefact=ARTEFACT, blob="ab12cd34-x.json", uploaded_at=at, old_keys=list(old),
                     gone_text=gone, old_hashes=[ic.content_hash(t) for t in OLD_TEXTS])


def _replaced(n: int, text: str = "new words") -> list[dict]:
    return _docs(ARTEFACT, n, text)


def test_watch_changed_passes_once_the_index_holds_exactly_the_new_keys_and_none_of_the_old(clock):
    world = World(clock)
    upload = _changed(clock.now)
    world.schedule(clock.now - 10, ARTEFACT, [{"chunk_id": k, "content": t} for k, t in zip(OLD, OLD_TEXTS)])
    world.schedule(clock.now + 20, ARTEFACT, _replaced(2) + [{"chunk_id": "9003", "content": "the cut tail"}])
    world.schedule(clock.now + 30, ARTEFACT, _replaced(2))

    watched = ic.watch_changed(upload, world.read, clock=clock, sleep=clock.sleep)

    assert watched.watched and watched.observed_after == 30 and watched.problem is None and watched.new_count == 2
    result = ic.i2_changed_replaces(watched)
    assert (result.check, result.passed) == ("I2", True)
    assert "2 chunks" in result.detail and "4 old keys" in result.detail


def test_i2_needs_no_count_typed_in_whatever_n_the_ingest_wrote():
    for n in (1, 3):
        world_docs = _replaced(n)
        upload = _changed()
        got = ic._problems_changed(upload, world_docs)
        assert got == []


def test_watch_changed_records_an_old_numeric_key_left_behind(clock):
    world = World(clock)
    world.schedule(clock.now + 5, ARTEFACT, _replaced(2) + [{"chunk_id": "9004", "content": "x"}])
    watched = ic.watch_changed(_changed(clock.now), world.read, clock=clock, sleep=clock.sleep)
    assert watched.observed_after is None and "old key" in watched.problem
    assert ic.i2_changed_replaces(watched).passed is False


def test_watch_changed_records_keys_that_are_not_contiguous_from_zero(clock):
    world = World(clock)
    world.schedule(clock.now + 5, ARTEFACT, [{"chunk_id": ic.chunk_key(ARTEFACT, 1), "content": "new"}])
    watched = ic.watch_changed(_changed(clock.now), world.read, clock=clock, sleep=clock.sleep)
    assert watched.observed_after is None and "keys" in watched.problem


def test_watch_changed_records_old_text_still_present(clock):
    world = World(clock)
    world.schedule(clock.now + 5, ARTEFACT, [{"chunk_id": k, "content": "kept the cut tail here"} for k in _keys(ARTEFACT, 2)])
    watched = ic.watch_changed(_changed(clock.now), world.read, clock=clock, sleep=clock.sleep)
    assert watched.observed_after is None and "old text" in watched.problem


def test_watch_changed_needs_some_new_text_not_only_chunks_the_old_version_held(clock):
    world = World(clock)
    world.schedule(clock.now + 5, ARTEFACT, [{"chunk_id": ic.chunk_key(ARTEFACT, 0), "content": "old head"},
                                             {"chunk_id": ic.chunk_key(ARTEFACT, 1), "content": "old middle"}])
    watched = ic.watch_changed(_changed(clock.now), world.read, clock=clock, sleep=clock.sleep)
    assert watched.observed_after is None and "new text" in watched.problem


def test_watch_changed_is_not_a_pass_when_the_input_was_not_shortened_enough(clock):
    world = World(clock)
    world.schedule(clock.now + 5, ARTEFACT, _replaced(4))
    upload = _changed(clock.now)
    watched = ic.watch_changed(upload, world.read, clock=clock, sleep=clock.sleep)
    result = ic.i2_changed_replaces(watched)
    assert result.passed is False and "not shortened enough" in result.detail and "I2 not measured" in result.detail
    assert clock.now < upload.uploaded_at + 120          # it did not wait out the window for a settled state


def test_watch_changed_records_nothing_indexed_in_time(clock):
    watched = ic.watch_changed(_changed(clock.now), lambda a: [], clock=clock, sleep=clock.sleep)
    result = ic.i2_changed_replaces(watched)
    assert watched.watched and result.passed is False and "0 chunks" in result.detail


def test_i2_never_judges_an_upload_it_did_not_watch():
    with pytest.raises(ic.NotWatchedError):
        ic.i2_changed_replaces(_changed())


def test_i2_fails_when_no_old_keys_were_recorded():
    watched = replace(_changed(old=()), watched=True, observed_after=3.0, new_count=1)
    result = ic.i2_changed_replaces(watched)
    assert result.passed is False and "old keys" in result.detail


# --- I3 -----------------------------------------------------------------------------------------------------

def _duplicate(at: float, snapshot: list[str] | None = None) -> ic.Upload:
    return ic.Upload(kind="duplicate", artefact=ARTEFACT, blob="ab12cd34-x.json", uploaded_at=at - 30,
                     expected_chunks=3, keys_after_first=_keys(ARTEFACT, 3) if snapshot is None else snapshot,
                     second_uploaded_at=at)


def test_i3_waits_out_the_second_event_then_passes_when_count_and_keys_are_unchanged(clock):
    world = World(clock)
    upload = _duplicate(clock.now)
    world.schedule(clock.now - 60, ARTEFACT, _docs(ARTEFACT, 3))
    result = ic.i3_duplicate_harmless(upload, world.read, clock=clock, sleep=clock.sleep)

    assert (result.check, result.passed) == ("I3", True)
    assert sum(clock.sleeps) >= ic.SETTLE_S
    assert "3 chunks" in result.detail


def test_i3_run_long_after_the_second_upload_does_not_wait_and_still_judges_the_state(clock):
    world = World(clock)
    world.schedule(clock.now - 60, ARTEFACT, _docs(ARTEFACT, 3))
    upload = _duplicate(clock.now - 3600)
    assert ic.i3_duplicate_harmless(upload, world.read, clock=clock, sleep=clock.sleep).passed is True
    assert clock.sleeps == []


def test_i3_fails_when_a_second_set_of_chunks_appeared(clock):
    world = World(clock)
    world.schedule(clock.now - 60, ARTEFACT, _docs(ARTEFACT, 3) + [{"chunk_id": "zzz-0", "content": "dup"}])
    result = ic.i3_duplicate_harmless(_duplicate(clock.now), world.read, clock=clock, sleep=clock.sleep)
    assert result.passed is False and "4 chunks" in result.detail


def test_i3_fails_when_a_key_changed_even_at_the_same_count(clock):
    world = World(clock)
    world.schedule(clock.now - 60, ARTEFACT, _docs(ARTEFACT, 2) + [{"chunk_id": "other", "content": "x"}])
    result = ic.i3_duplicate_harmless(_duplicate(clock.now), world.read, clock=clock, sleep=clock.sleep)
    assert result.passed is False and "keys" in result.detail


def test_i3_fails_without_a_snapshot_from_the_first_upload(clock):
    world = World(clock)
    world.schedule(clock.now - 60, ARTEFACT, _docs(ARTEFACT, 3))
    result = ic.i3_duplicate_harmless(_duplicate(clock.now, snapshot=[]), world.read, clock=clock, sleep=clock.sleep)
    assert result.passed is False and "first upload" in result.detail


# --- I4: judged by the message's own insertion time -------------------------------------------------------------

BLOB = "ab12cd34-broken-1a2b3c.json"


def _broken(at: float, docs_before: int = 1000, blob: str = BLOB) -> ic.Upload:
    return ic.Upload(kind="broken", artefact="", blob=blob, uploaded_at=at, docs_before=docs_before)


def _msg(blob: str, inserted_at: float | None, *, encoded: bool = False) -> ic.PoisonMessage:
    text = json.dumps(_event(blob))
    return ic.PoisonMessage(base64.b64encode(text.encode()).decode() if encoded else text, inserted_at)


class Queue:
    """A poison queue that gains a message for `blob` when the fake clock reaches `arrives_at`, stamped with that time."""

    def __init__(self, clock, arrives_at: float | None, blob: str = BLOB, *, encoded: bool = False) -> None:
        self.clock, self.arrives_at, self.blob, self.encoded = clock, arrives_at, blob, encoded
        self.peeks = 0

    def peek(self) -> list[ic.PoisonMessage]:
        self.peeks += 1
        other = _msg("ab12cd34-someone-else.json", self.clock.now - 5)
        if self.arrives_at is None or self.clock.now < self.arrives_at:
            return [other]
        return [other, _msg(self.blob, self.arrives_at, encoded=self.encoded)]


@pytest.mark.parametrize("encoded", [False, True])
def test_i4_passes_when_the_poison_message_was_inserted_within_10_minutes_and_the_index_gained_nothing(clock, encoded):
    upload = _broken(clock.now)
    queue = Queue(clock, clock.now + 240, encoded=encoded)
    result = ic.i4_broken_to_poison(upload, queue.peek, lambda: 1000, clock=clock, sleep=clock.sleep)
    assert (result.check, result.passed) == ("I4", True) and "240 s" in result.detail


def test_i4_run_hours_later_judges_by_the_insertion_time_and_peeks_at_least_once(clock):
    upload = _broken(clock.now)
    inserted = clock.now + 200
    clock.now += 3 * 3600
    result = ic.i4_broken_to_poison(upload, lambda: [_msg(BLOB, inserted)], lambda: 1000, clock=clock, sleep=clock.sleep)
    assert result.passed is True and "200 s" in result.detail and clock.sleeps == []


def test_i4_run_late_with_a_message_inserted_after_10_minutes_fails_by_its_time_not_the_runs(clock):
    upload = _broken(clock.now)
    inserted = clock.now + 700
    clock.now += 7200
    result = ic.i4_broken_to_poison(upload, lambda: [_msg(BLOB, inserted)], lambda: 1000, clock=clock, sleep=clock.sleep)
    assert result.passed is False and "700 s" in result.detail and "10 minutes" in result.detail


def test_i4_passes_a_message_inserted_at_exactly_ten_minutes(clock):
    upload = _broken(clock.now)
    assert ic.i4_broken_to_poison(upload, lambda: [_msg(BLOB, clock.now + 600)], lambda: 1000,
                                  clock=clock, sleep=clock.sleep).passed is True


def test_i4_run_after_the_window_with_no_message_peeks_once_and_fails(clock):
    upload = _broken(clock.now)
    clock.now += 7200
    queue = Queue(clock, None)
    result = ic.i4_broken_to_poison(upload, queue.peek, lambda: 1000, clock=clock, sleep=clock.sleep)
    assert result.passed is False and queue.peeks == 1 and "10 minutes" in result.detail


def test_i4_polls_inside_its_window_until_a_message_arrives(clock):
    upload = _broken(clock.now)
    queue = Queue(clock, clock.now + 100)
    ic.i4_broken_to_poison(upload, queue.peek, lambda: 1000, clock=clock, sleep=clock.sleep)
    assert queue.peeks > 3


def test_i4_fails_when_only_other_uploads_reach_the_poison_queue(clock):
    queue = Queue(clock, None)
    result = ic.i4_broken_to_poison(_broken(clock.now), queue.peek, lambda: 1000, clock=clock, sleep=clock.sleep)
    assert result.passed is False and queue.peeks > 5


def test_a_second_broken_upload_is_not_matched_by_the_first_ones_message(clock):
    first = _msg("ab12cd34-broken-aaaaaa.json", clock.now + 30)
    second = _broken(clock.now, blob="ab12cd34-broken-bbbbbb.json")
    result = ic.i4_broken_to_poison(second, lambda: [first], lambda: 1000, clock=clock, sleep=clock.sleep)
    assert result.passed is False


def test_i4_fails_when_the_index_gained_documents(clock):
    upload = _broken(clock.now)
    queue = Queue(clock, clock.now + 100)
    result = ic.i4_broken_to_poison(upload, queue.peek, lambda: 1003, clock=clock, sleep=clock.sleep)
    assert result.passed is False and "gained" in result.detail


def test_i4_without_an_insertion_time_falls_back_to_when_it_was_first_seen(clock):
    upload = _broken(clock.now)
    state = {"seen": False}

    def peek():
        if clock.now >= upload.uploaded_at + 90:
            state["seen"] = True
            return [_msg(BLOB, None)]
        return []

    result = ic.i4_broken_to_poison(upload, peek, lambda: 1000, clock=clock, sleep=clock.sleep)
    assert result.passed is True and state["seen"]


def test_the_module_only_peeks_and_never_asks_for_anything_else():
    source = open(ic.__file__, encoding="utf-8").read()
    assert "DELETE" not in source and '"delete"' not in source.lower() and "popreceipt" not in source.lower()
