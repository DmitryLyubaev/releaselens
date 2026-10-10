"""I1 to I4 on fixtures, and the REST calls under them: the index by artefact, the blob upload, the queue peek."""

import base64
import json

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

def test_upload_blob_puts_a_block_blob_to_artefacts_in_with_the_bearer_token():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201)

    ic.upload_blob(_index_client(handler), BLOB_ENDPOINT, lambda: TOKEN, "ab12cd34-xyz.json", b'{"a": 1}')

    request = seen[0]
    assert request.method == "PUT" and request.url.path == "/artefacts-in/ab12cd34-xyz.json"
    assert request.headers["x-ms-blob-type"] == "BlockBlob" and "x-ms-version" in request.headers
    assert request.headers["authorization"] == f"Bearer {TOKEN}" and request.content == b'{"a": 1}'


def test_upload_blob_raises_on_anything_but_201_with_the_status_only():
    with pytest.raises(ic.UploadError) as raised:
        ic.upload_blob(_index_client(lambda r: httpx.Response(403, text="https://secret.example.com")),
                       BLOB_ENDPOINT, lambda: TOKEN, "n.json", b"{}")
    assert str(raised.value) == "the blob upload answered 403"


def _event(blob: str) -> dict:
    return {"topic": "t", "subject": f"/blobServices/default/containers/artefacts-in/blobs/{blob}",
            "eventType": "Microsoft.Storage.BlobCreated",
            "data": {"url": f"https://store.example.com/artefacts-in/{blob}"}}


def _queue_xml(texts: list[str]) -> str:
    messages = "".join(f"<QueueMessage><MessageId>m{i}</MessageId><MessageText>{text}</MessageText></QueueMessage>"
                       for i, text in enumerate(texts))
    return f'<?xml version="1.0" encoding="utf-8"?><QueueMessagesList>{messages}</QueueMessagesList>'


def test_peek_poison_only_peeks_and_returns_the_message_texts():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text=_queue_xml(["one", "two"]))

    texts = ic.peek_poison(_index_client(handler), QUEUE_ENDPOINT, lambda: TOKEN)

    assert texts == ["one", "two"]
    request = seen[0]
    assert request.method == "GET" and request.url.path == "/ingest-events-poison/messages"
    assert request.url.params["peekonly"] == "true" and request.url.params["numofmessages"] == "32"
    assert request.headers["authorization"] == f"Bearer {TOKEN}" and "x-ms-version" in request.headers
    assert len(seen) == 1


def test_peek_poison_reads_an_empty_queue_and_raises_on_a_refusal():
    assert ic.peek_poison(_index_client(lambda r: httpx.Response(200, text="<QueueMessagesList />")),
                          QUEUE_ENDPOINT, lambda: TOKEN) == []
    with pytest.raises(ic.QueueReadError) as raised:
        ic.peek_poison(_index_client(lambda r: httpx.Response(403, text="x")), QUEUE_ENDPOINT, lambda: TOKEN)
    assert str(raised.value) == "the queue answered 403"


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


# --- I1 ----------------------------------------------------------------------------------------------

def _new(artefact: str, expected: int, at: float, n: int = 0) -> ic.Upload:
    stem = base64.urlsafe_b64encode(artefact.encode()).rstrip(b"=").decode()
    return ic.Upload(kind="new", artefact=artefact, blob=ic.blob_name("ab12cd34", stem), uploaded_at=at,
                     expected_chunks=expected)


def _five(at: float) -> list[ic.Upload]:
    return [_new(f"issue:{n}", n % 3 + 1, at) for n in range(1, 6)]


def test_i1_passes_when_every_artefact_is_searchable_in_time_and_reports_median_and_maximum(clock):
    world = World(clock)
    uploads = _five(clock.now)
    for n, upload in enumerate(uploads):
        world.schedule(clock.now + 10 * (n + 1), upload.artefact, _docs(upload.artefact, upload.expected_chunks))

    result = ic.i1_new_searchable(uploads, world.read, clock=clock, sleep=clock.sleep)

    assert (result.check, result.passed) == ("I1", True)
    assert "5 of 5" in result.detail and "median 30 s" in result.detail and "maximum 50 s" in result.detail


def test_i1_passes_at_exactly_120_seconds_and_fails_at_125(clock):
    on_time, late = World(clock), World(clock)
    upload = _new("issue:1", 2, clock.now)
    on_time.schedule(clock.now + 120, upload.artefact, _docs(upload.artefact, 2))
    late.schedule(clock.now + 125, upload.artefact, _docs(upload.artefact, 2))

    start = clock.now
    assert ic.i1_new_searchable([upload], on_time.read, clock=clock, sleep=clock.sleep).passed is True
    clock.now = start
    result = ic.i1_new_searchable([upload], late.read, clock=clock, sleep=clock.sleep)
    assert result.passed is False and "120 s" in result.detail and "issue:1" in result.detail


def test_i1_fails_when_the_chunk_count_is_not_the_expected_one(clock):
    world = World(clock)
    upload = _new("issue:1", 4, clock.now)
    world.schedule(clock.now + 5, upload.artefact, _docs(upload.artefact, 3))
    result = ic.i1_new_searchable([upload], world.read, clock=clock, sleep=clock.sleep)
    assert result.passed is False and "3 of 4" in result.detail


def test_i1_fails_when_the_chunks_hold_keys_of_another_scheme(clock):
    world = World(clock)
    upload = _new("issue:1", 2, clock.now)
    world.schedule(clock.now + 5, upload.artefact, [{"chunk_id": "101", "content": "a"}, {"chunk_id": "102", "content": "b"}])
    assert ic.i1_new_searchable([upload], world.read, clock=clock, sleep=clock.sleep).passed is False


def test_i1_fails_with_no_uploads(clock):
    assert ic.i1_new_searchable([], lambda a: [], clock=clock, sleep=clock.sleep).passed is False


def test_i1_polls_on_the_injected_sleep_and_never_for_real(clock):
    world = World(clock)
    upload = _new("issue:1", 1, clock.now)
    world.schedule(clock.now + 40, upload.artefact, _docs(upload.artefact, 1))
    ic.i1_new_searchable([upload], world.read, clock=clock, sleep=clock.sleep)
    assert clock.sleeps and all(s > 0 for s in clock.sleeps) and world.reads > 2


# --- I2 ----------------------------------------------------------------------------------------------

def _changed(at: float, *, expected: int = 2, old: tuple[str, ...] = ("9001", "9002", "9003", "9004"),
             gone: str = "the cut tail") -> ic.Upload:
    return ic.Upload(kind="changed", artefact=ARTEFACT, blob="ab12cd34-x.json", uploaded_at=at,
                     expected_chunks=expected, old_keys=list(old), gone_text=gone)


def test_i2_passes_when_the_index_holds_exactly_the_new_chunks_and_none_of_the_old_keys(clock):
    world = World(clock)
    upload = _changed(clock.now)
    world.schedule(clock.now - 10, ARTEFACT, [{"chunk_id": k, "content": "the cut tail"} for k in upload.old_keys])
    world.schedule(clock.now + 20, ARTEFACT, _docs(ARTEFACT, 2) + [{"chunk_id": "9003", "content": "the cut tail"}])
    world.schedule(clock.now + 30, ARTEFACT, _docs(ARTEFACT, 2, "new words"))

    result = ic.i2_changed_replaces(upload, world.read, clock=clock, sleep=clock.sleep)

    assert (result.check, result.passed) == ("I2", True)
    assert "2 chunks" in result.detail and "4 old keys" in result.detail


def test_i2_fails_when_an_old_numeric_key_is_left(clock):
    world = World(clock)
    upload = _changed(clock.now)
    world.schedule(clock.now + 5, ARTEFACT, _docs(ARTEFACT, 2) + [{"chunk_id": "9004", "content": "x"}])
    result = ic.i2_changed_replaces(upload, world.read, clock=clock, sleep=clock.sleep)
    assert result.passed is False and "old key" in result.detail


def test_i2_fails_when_there_are_more_chunks_than_the_new_text_yields(clock):
    world = World(clock)
    upload = _changed(clock.now)
    world.schedule(clock.now + 5, ARTEFACT, _docs(ARTEFACT, 3))
    result = ic.i2_changed_replaces(upload, world.read, clock=clock, sleep=clock.sleep)
    assert result.passed is False and "3 chunks, expected 2" in result.detail


def test_i2_fails_when_the_old_text_is_still_in_a_chunk(clock):
    world = World(clock)
    upload = _changed(clock.now)
    world.schedule(clock.now + 5, ARTEFACT, [{"chunk_id": k, "content": "kept the cut tail here"} for k in _keys(ARTEFACT, 2)])
    result = ic.i2_changed_replaces(upload, world.read, clock=clock, sleep=clock.sleep)
    assert result.passed is False and "old text" in result.detail


def test_i2_fails_when_nothing_is_indexed_in_time(clock):
    result = ic.i2_changed_replaces(_changed(clock.now), lambda a: [], clock=clock, sleep=clock.sleep)
    assert result.passed is False and "0 chunks" in result.detail


# --- I3 ----------------------------------------------------------------------------------------------

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


# --- I4 ----------------------------------------------------------------------------------------------

def _broken(at: float, docs_before: int = 1000) -> ic.Upload:
    return ic.Upload(kind="broken", artefact="", blob="ab12cd34-broken.json", uploaded_at=at, docs_before=docs_before)


class Queue:
    def __init__(self, clock, arrives_at: float | None, blob: str, *, encoded: bool = False) -> None:
        self.clock, self.arrives_at, self.blob, self.encoded = clock, arrives_at, blob, encoded
        self.peeks = 0

    def peek(self) -> list[str]:
        self.peeks += 1
        other = json.dumps(_event("ab12cd34-someone-else.json"))
        if self.arrives_at is None or self.clock.now < self.arrives_at:
            return [other]
        text = json.dumps(_event(self.blob))
        return [other, base64.b64encode(text.encode()).decode() if self.encoded else text]


@pytest.mark.parametrize("encoded", [False, True])
def test_i4_passes_when_the_poison_message_arrives_within_10_minutes_and_the_index_gained_nothing(clock, encoded):
    upload = _broken(clock.now)
    queue = Queue(clock, clock.now + 240, upload.blob, encoded=encoded)
    result = ic.i4_broken_to_poison(upload, queue.peek, lambda: 1000, clock=clock, sleep=clock.sleep)
    assert (result.check, result.passed) == ("I4", True)
    assert "240 s" in result.detail


def test_i4_passes_at_ten_minutes_and_fails_a_little_after(clock):
    start = clock.now
    on_time = ic.i4_broken_to_poison(_broken(start), Queue(clock, start + 600, "ab12cd34-broken.json").peek,
                                     lambda: 1000, clock=clock, sleep=clock.sleep)
    clock.now = start
    late = ic.i4_broken_to_poison(_broken(start), Queue(clock, start + 660, "ab12cd34-broken.json").peek,
                                  lambda: 1000, clock=clock, sleep=clock.sleep)
    assert on_time.passed is True
    assert late.passed is False and "10 minutes" in late.detail


def test_i4_fails_when_only_other_uploads_reach_the_poison_queue(clock):
    queue = Queue(clock, None, "ab12cd34-broken.json")
    result = ic.i4_broken_to_poison(_broken(clock.now), queue.peek, lambda: 1000, clock=clock, sleep=clock.sleep)
    assert result.passed is False and queue.peeks > 5


def test_i4_fails_when_the_index_gained_documents(clock):
    upload = _broken(clock.now)
    queue = Queue(clock, clock.now + 100, upload.blob)
    result = ic.i4_broken_to_poison(upload, queue.peek, lambda: 1003, clock=clock, sleep=clock.sleep)
    assert result.passed is False and "gained" in result.detail


def test_i4_only_peeks_and_never_asks_for_anything_else():
    # The poison read is a function of the REST call alone: there is no dequeue or delete in the module.
    source = open(ic.__file__, encoding="utf-8").read()
    assert "DELETE" not in source and '"delete"' not in source.lower() and "popreceipt" not in source.lower()


def test_i2_fails_when_no_old_keys_were_recorded_because_nothing_could_have_been_replaced(clock):
    upload = _changed(clock.now, old=())
    result = ic.i2_changed_replaces(upload, lambda a: _docs(ARTEFACT, 2), clock=clock, sleep=clock.sleep)
    assert result.passed is False and "old keys" in result.detail
