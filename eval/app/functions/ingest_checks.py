"""The ingestion checks I1 to I4 (spec §7), and the REST calls under them.

Each check takes what it measures as a function (the index read by artefact, the poison queue's
peek, a document count), and a clock and a sleep it is given, so it runs on fixtures and never
waits for real. The REST calls are as the owner, with a bearer token and no key:

- the index is read by artefact with a filter and no `top`, paging the way the C# client does
  (`SearchIndexClient.KeysForArtefactAsync`): follow `@search.nextPageParameters`' `skip`, send the
  first request's filter every time, and refuse a page that does not move forward;
- an upload is one Put Blob to `artefacts-in`, named `<session id>-<base64url key>.json`, a name
  that needs no percent-encoding;
- the poison queue is only ever peeked: the call is `peekonly=true`, so no message is made
  invisible, counted or removed, and the queue is as it was.

Nothing here puts a token, a URL or a reply body into a message or a result: a refusal is its status.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
import statistics
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from email.utils import parsedate_to_datetime

import httpx

from app.gateway.checks import CheckResult
from app.retrieval import search_index

CONTAINER = "artefacts-in"
POISON_QUEUE = "ingest-events-poison"
STORAGE_SCOPE = "https://storage.azure.com/.default"
STORAGE_VERSION = "2021-08-06"
PEEK_MAX = 32               # the most messages one peek returns

I1_TIMEOUT_S = 120          # spec §7: each artefact's chunks in the index within 120 s of its upload
I2_TIMEOUT_S = 120          # spec §7 sets no time for I2: the same 120 s
I4_TIMEOUT_S = 600          # spec §7: a poison message within 10 minutes
POLL_S = 5
POISON_POLL_S = 15
SETTLE_S = 60               # I3: how long after the second upload before the index is read
MAX_PAGES = 100             # as the C# client: 5,000 chunks of one artefact is a bug, not an artefact

Clock = Callable[[], float]
Sleep = Callable[[float], None]
ReadChunks = Callable[[str], list[dict]]
Token = Callable[[], str]

_STEM = re.compile(r"[A-Za-z0-9_-]+")
_KEY = re.compile(r"(?:commit|issue|pull_request|release):.+", re.DOTALL)


class IndexReadError(Exception):
    """The index could not be read; the message holds a status, never a URL or a body.

    `status` is the HTTP status when the index answered with one, and None when the read failed otherwise.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def is_transient(error: Exception) -> bool:
    """Whether a failed index read may succeed if tried again: a 429, a 5xx, a timeout or a lost connection.

    Anything else, a 401, a 403 or another 4xx above all, is not: wrong roles or a wrong audience fail at once.
    """
    if isinstance(error, IndexReadError):
        return error.status is not None and (error.status == 429 or 500 <= error.status <= 599)
    return isinstance(error, httpx.TransportError)


class UploadError(Exception):
    """A blob upload that did not return 201."""


class QueueReadError(Exception):
    """The queue could not be peeked."""


class NotWatchedError(Exception):
    """An upload `ingest-checks` was asked to judge was not watched through its window by `upload`."""


@dataclass(frozen=True)
class PoisonMessage:
    text: str
    inserted_at: float | None       # epoch seconds, from the peek's InsertionTime; None if the reply had none


@dataclass(frozen=True)
class Upload:
    """What one upload recorded, for the check that reads it later.

    Holds only what the checks need: an artefact's ID, a blob name, times, counts and chunk keys.
    """

    kind: str                                   # new | changed | duplicate | broken
    artefact: str
    blob: str
    uploaded_at: float                          # wall-clock seconds, after the upload returned 201
    expected_chunks: int | None = None
    old_keys: list[str] = field(default_factory=list)           # changed: the keys before the upload
    gone_text: str | None = None                                # changed: a phrase only the old text held
    old_hashes: list[str] = field(default_factory=list)         # changed: SHA-256 of each old chunk's content
    keys_after_first: list[str] = field(default_factory=list)   # duplicate: the keys once the first upload landed
    second_uploaded_at: float | None = None                     # duplicate
    docs_before: int | None = None                              # broken: the index's document count
    # What `upload` saw while it watched the artefact's window (new and changed). An upload that was not
    # watched to the end of its window cannot be judged later: the window is over, and a late read says
    # nothing about it.
    watched: bool = False
    observed_after: float | None = None     # seconds from the upload until the index first held the expected chunks
    problem: str | None = None              # what was still wrong when the window ended, or settled wrong
    new_count: int | None = None            # changed: the chunks the index holds after the replacement
    # The index reads made while watching, and how many of them failed with a transient error (a 429, a
    # 5xx, a timeout or a lost connection), each counted as "not seen yet". When every read failed, the
    # window was not watched at all, and the upload stays unwatched.
    polls: int = 0
    failed_polls: int = 0


# --- names and keys ---------------------------------------------------------------------------------

def chunk_key(artefact: str, index: int) -> str:
    """The key of chunk `index` of `artefact`: the artefact in base64url without padding, a dash, the index.

    The same rule as `ChunkKeys.For` in `ReleaseLens.Functions.Common`.
    """
    return f"{base64.urlsafe_b64encode(artefact.encode('utf-8')).rstrip(b'=').decode('ascii')}-{index}"


def new_session_id() -> str:
    """Eight lowercase hex characters: short, and not a GUID, so a written file may hold it."""
    return secrets.token_hex(4)


def blob_name(session: str, stem: str) -> str:
    """`<session id>-<base64url key>.json`: letters, digits, `-` and `_` only."""
    return f"{session}-{stem}.json"


def artefact_of_stem(stem: str) -> str:
    """The artefact a file named `<base64url key>.json` holds, or ValueError when the stem is not a key."""
    if not _STEM.fullmatch(stem):
        raise ValueError("the file name is not a base64url artefact key")
    try:
        artefact = base64.urlsafe_b64decode(stem + "=" * (-len(stem) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        raise ValueError("the file name is not a base64url artefact key") from None
    if not _KEY.fullmatch(artefact) or base64.urlsafe_b64encode(artefact.encode()).rstrip(b"=").decode() != stem:
        raise ValueError("the file name is not a base64url artefact key")
    return artefact


# --- REST: the index, the blob container, the poison queue ----------------------------------------------

def chunks_for_artefact(http: httpx.Client, endpoint: str, token: Token, artefact: str) -> list[dict]:
    """Every chunk of `artefact`, as `{"chunk_id", "content"}`, whatever scheme wrote its key.

    No `top` is sent: the service adds a next-page link only when it cannot return the `top` asked
    for, and with none it pages by its own size and always says where the next page is.
    """
    url = f"{endpoint.rstrip('/')}/indexes/{search_index.INDEX_NAME}/docs/search"
    body: dict = {"search": "*", "filter": f"artefact eq '{artefact.replace(chr(39), chr(39) * 2)}'",
                  "select": "chunk_id,content"}
    chunks: list[dict] = []
    skip = 0
    for page in range(1, MAX_PAGES + 1):
        response = http.post(url, params={"api-version": search_index.API_VERSION},
                             headers={"Authorization": f"Bearer {token()}"}, json=body)
        if not response.is_success:
            raise IndexReadError(f"the index answered {response.status_code}", status=response.status_code)
        reply = response.json()
        chunks += [{"chunk_id": d["chunk_id"], "content": d.get("content") or ""} for d in reply.get("value", [])]
        if "@odata.nextLink" not in reply:
            return chunks
        # Only the skip is taken from the reply: the filter stays the first one's, whatever is echoed.
        next_skip = (reply.get("@search.nextPageParameters") or {}).get("skip")
        if type(next_skip) is not int or next_skip <= skip:
            raise IndexReadError("the index paged without moving forward")
        skip = next_skip
        body["skip"] = skip
    raise IndexReadError(f"the index still had more chunks after {MAX_PAGES} pages")


def upload_blob(http: httpx.Client, endpoint: str, token: Token, name: str, data: bytes, *,
                overwrite: bool = False) -> None:
    """Put one blob in `artefacts-in`, as the owner.

    Unless `overwrite`, the put carries `If-None-Match: *`, so a blob that already exists is never replaced
    (a repeated name would be matched by the earlier upload's event); `duplicate` overwrites on purpose.
    """
    headers = {"Authorization": f"Bearer {token()}", "x-ms-version": STORAGE_VERSION,
               "x-ms-blob-type": "BlockBlob", "Content-Type": "application/json"}
    if not overwrite:
        headers["If-None-Match"] = "*"
    response = http.put(f"{endpoint.rstrip('/')}/{CONTAINER}/{name}", headers=headers, content=data)
    if response.status_code in (409, 412) and not overwrite:
        raise UploadError("a blob of that name already exists in this session; start a new session "
                          "(--new-session) rather than upload the same name again")
    if response.status_code != 201:
        raise UploadError(f"the blob upload answered {response.status_code}")


def _queue_url(endpoint: str, suffix: str = "") -> str:
    return f"{endpoint.rstrip('/')}/{POISON_QUEUE}{suffix}"


def peek_poison(http: httpx.Client, endpoint: str, token: Token) -> list[PoisonMessage]:
    """The first messages in the poison queue, peeked: nothing is dequeued or changed.

    One peek returns at most 32 messages, from the head of the queue, so a longer queue could hide a new
    message: the queue's approximate count (Get Queue Metadata, which Reader allows) is read first, and a
    queue over 32 is refused with the count, not peeked.
    """
    headers = {"Authorization": f"Bearer {token()}", "x-ms-version": STORAGE_VERSION}
    meta = http.get(_queue_url(endpoint), params={"comp": "metadata"}, headers=headers)
    if meta.status_code != 200:
        raise QueueReadError(f"the queue answered {meta.status_code}")
    try:
        held = int(meta.headers["x-ms-approximate-messages-count"])
    except (KeyError, ValueError):
        raise QueueReadError("the queue did not say how many messages it holds") from None
    if held > PEEK_MAX:
        raise QueueReadError(f"the poison queue holds {held} messages, more than the {PEEK_MAX} one peek returns, "
                             "so a new message could be hidden. Clearing the queue needs a role the owner does "
                             "not hold; its messages expire after the queue's message time-to-live (7 days by "
                             "default). See the runbook's step 6a")
    response = http.get(_queue_url(endpoint, "/messages"),
                        params={"peekonly": "true", "numofmessages": str(PEEK_MAX)}, headers=headers)
    if response.status_code != 200:
        raise QueueReadError(f"the queue answered {response.status_code}")
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        raise QueueReadError("the queue's answer is not XML") from None
    return [PoisonMessage(message.findtext("MessageText") or "", _inserted_at(message.findtext("InsertionTime")))
            for message in root.iter("QueueMessage")]


def _inserted_at(text: str | None) -> float | None:
    try:
        return parsedate_to_datetime(text).timestamp() if text else None
    except (TypeError, ValueError):
        return None


def blob_of_message(text: str) -> str | None:
    """The blob name inside a queue message's Event Grid event, or None when it holds none.

    The message is the event as plain JSON or as base64 of it; it is a single event or a list.
    """
    value = None
    for candidate in (text, _unbase64(text)):
        if candidate is None:
            continue
        try:
            value = json.loads(candidate)
            break
        except ValueError:
            continue
    events = value if isinstance(value, list) else [value]
    for event in events:
        subject = event.get("subject") if isinstance(event, dict) else None
        if isinstance(subject, str) and "/blobs/" in subject:
            return subject.split("/blobs/", 1)[1]
    return None


def _unbase64(text: str) -> str | None:
    try:
        return base64.b64decode(text, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


# --- the checks ---------------------------------------------------------------------------------------

def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _expected_keys(upload: Upload) -> list[str]:
    return [chunk_key(upload.artefact, i) for i in range(upload.expected_chunks or 0)]


def _problems_new(upload: Upload, chunks: list[dict]) -> str | None:
    """Why `chunks` are not the chunks a finished ingestion of `upload` leaves, or None."""
    expected = upload.expected_chunks or 0
    if len(chunks) != expected:
        return f"{len(chunks)} of {expected} chunks"
    if {c["chunk_id"] for c in chunks} != set(_expected_keys(upload)) or not all(c["content"] for c in chunks):
        return "the keys are not the ones the ingest rule writes, or a chunk is empty"
    return None


def _poll(read_chunks: ReadChunks, artefact: str) -> list[dict] | None:
    """One read of the index while watching: None when it failed with a transient error.

    Any other failure (a 401, a 403, another 4xx, a page that did not move forward) propagates at once, so
    wrong roles or a wrong audience are never turned into a slow fail.
    """
    try:
        return read_chunks(artefact)
    except (IndexReadError, httpx.TransportError) as error:
        if is_transient(error):
            return None
        raise


def _all_failed(polls: int) -> str:
    return f"every one of the {polls} polls failed with a transient error: the window was not watched"


def watch_new(uploads: list[Upload], read_chunks: ReadChunks, *, clock: Clock, sleep: Sleep,
              timeout_s: float = I1_TIMEOUT_S, interval_s: float = POLL_S) -> list[Upload]:
    """Watch each new upload's window (120 s from its own upload) and record what was seen, per upload.

    Called by `upload`, straight after the uploads, so the window is always watched while it is open. A read
    that fails with a transient error counts as "not seen yet" and the polling goes on; an upload whose every
    read failed stays unwatched, so it is never judged.
    """
    pending = set(range(len(uploads)))
    seen: dict[int, float] = {}
    last: dict[int, str] = {}
    polls = [0] * len(uploads)
    failed = [0] * len(uploads)
    while True:
        live = [i for i in sorted(pending) if clock() <= uploads[i].uploaded_at + timeout_s]
        if not live:
            break
        for i in live:
            upload = uploads[i]
            polls[i] += 1
            chunks = _poll(read_chunks, upload.artefact)
            if chunks is None:
                failed[i] += 1
                continue
            problem = _problems_new(upload, chunks)
            elapsed = clock() - upload.uploaded_at
            if problem is None and elapsed <= timeout_s:
                seen[i] = elapsed
                pending.discard(i)
            else:
                last[i] = problem or "searchable only after the time was up"
        if pending:
            sleep(interval_s)
    return [replace(u, watched=False, observed_after=None, problem=_all_failed(polls[i]),
                    polls=polls[i], failed_polls=failed[i])
            if polls[i] and failed[i] == polls[i] else
            replace(u, watched=True, observed_after=seen.get(i),
                    problem=None if i in seen else last.get(i, "never read in time"),
                    polls=polls[i], failed_polls=failed[i])
            for i, u in enumerate(uploads)]


def _failed_polls(uploads: list[Upload]) -> str:
    """How many of the watch's index reads failed with a transient error, for a result's detail."""
    polls = sum(u.polls for u in uploads)
    if not polls:
        return ""
    return (f"; {sum(u.failed_polls for u in uploads)} of {polls} polls failed with a transient error "
            "(counted as not seen yet)")


def require_watched(check: str, uploads: list[Upload]) -> None:
    unwatched = [u.artefact or u.blob for u in uploads if not u.watched]
    if unwatched:
        raise NotWatchedError(f"{check} was not watched through its window by `upload` for {', '.join(unwatched)}: "
                              "a read now says nothing about the window, so nothing is judged; upload again in a "
                              "new session and let `upload` finish")


def i1_new_searchable(uploads: list[Upload], *, timeout_s: float = I1_TIMEOUT_S) -> CheckResult:
    """I1: each new artefact's chunks were in the index, with the expected count, within 120 s of its upload.

    Judged from what `upload` saw while it watched each window; an upload that was not watched is refused.
    """
    if not uploads:
        return CheckResult("I1", False, "no new artefact was uploaded in this session")
    require_watched("I1", uploads)
    missed = [u for u in uploads if u.observed_after is None or u.observed_after > timeout_s]
    if missed:
        shown = "; ".join(f"{u.artefact}: {u.problem or 'searchable only after the time was up'}" for u in missed)
        return CheckResult("I1", False, f"{len(uploads) - len(missed)} of {len(uploads)} searchable within "
                                        f"{timeout_s:.0f} s; not: {shown}{_failed_polls(uploads)}")
    values = sorted(u.observed_after for u in uploads)
    return CheckResult("I1", True, f"{len(uploads)} of {len(uploads)} searchable within {timeout_s:.0f} s of "
                                   f"their upload: median {statistics.median(values):.0f} s, maximum "
                                   f"{values[-1]:.0f} s (seen at each poll, so at most {POLL_S} s late)"
                                   f"{_failed_polls(uploads)}")


NOT_SHORTENED = "the input was not shortened enough: I2 not measured"


def _problems_changed(upload: Upload, chunks: list[dict]) -> list[str]:
    """What is still wrong with `chunks` as the replacement of the changed artefact; empty when it is replaced.

    No count is typed in: the new chunks are whatever the index holds under the new keys, so the check is
    that those are exactly keys 0 to n-1 of the artefact, none of the old keys remain, n is smaller than the
    old count, the cut-away text is gone and some text is new.
    """
    n = len(chunks)
    if n == 0:
        return ["0 chunks in the index"]
    held = {c["chunk_id"] for c in chunks}
    problems = []
    old_left = held & set(upload.old_keys)
    if old_left:
        problems.append(f"{len(old_left)} old key(s) still in the index")
    elif held != {chunk_key(upload.artefact, i) for i in range(n)}:
        problems.append(f"the {n} keys are not exactly the new keys 0 to {n - 1}")
    if upload.gone_text and any(upload.gone_text in c["content"] for c in chunks):
        problems.append("old text still present")
    old = set(upload.old_hashes)
    if not any(content_hash(c["content"]) not in old for c in chunks):
        problems.append("no new text: every chunk is one the old version held")
    if not problems and n >= len(upload.old_keys):
        problems.append(NOT_SHORTENED)
    return problems


def watch_changed(upload: Upload, read_chunks: ReadChunks, *, clock: Clock, sleep: Sleep,
                  timeout_s: float = I2_TIMEOUT_S, interval_s: float = POLL_S) -> Upload:
    """Watch the changed artefact's window (spec §7 sets none for I2: the same 120 s) and record what was seen.

    As in `watch_new`, a read that fails with a transient error is "not seen yet", and an upload whose every
    read failed stays unwatched.
    """
    problems: list[str] = ["never read in time"]
    chunks: list[dict] = []
    polls = failed = 0
    while clock() <= upload.uploaded_at + timeout_s:
        polls += 1
        read = _poll(read_chunks, upload.artefact)
        if read is None:
            failed += 1
            sleep(interval_s)
            continue
        chunks = read
        problems = _problems_changed(upload, chunks)
        elapsed = clock() - upload.uploaded_at
        if not problems:
            return replace(upload, watched=True, observed_after=elapsed, problem=None, new_count=len(chunks),
                           polls=polls, failed_polls=failed)
        if problems == [NOT_SHORTENED]:
            break                       # settled: waiting longer changes nothing
        sleep(interval_s)
    if polls and failed == polls:
        return replace(upload, watched=False, observed_after=None, problem=_all_failed(polls),
                       polls=polls, failed_polls=failed)
    return replace(upload, watched=True, observed_after=None, problem="; ".join(problems), new_count=len(chunks),
                   polls=polls, failed_polls=failed)


def i2_changed_replaces(upload: Upload) -> CheckResult:
    """I2: the index holds exactly the changed artefact's new chunks, with new text, none of its old keys.

    Judged from what `upload` saw while it watched; an upload that was not watched is refused.
    """
    require_watched("I2", [upload])
    if not upload.old_keys:
        return CheckResult("I2", False, "the upload recorded no old keys: the artefact was not in the index before it")
    if upload.problem is not None:
        return CheckResult("I2", False, (upload.problem if upload.problem == NOT_SHORTENED
                                         else f"not replaced within {I2_TIMEOUT_S:.0f} s: {upload.problem}")
                           + _failed_polls([upload]))
    return CheckResult("I2", True, f"exactly {upload.new_count} chunks under the new keys, with new text and without "
                                   f"the cut-away text; {len(upload.old_keys)} old keys gone, {upload.observed_after:.0f} s "
                                   f"after the upload{_failed_polls([upload])}")


def i3_duplicate_harmless(upload: Upload, read_chunks: ReadChunks, *, clock: Clock, sleep: Sleep,
                          settle_s: float = SETTLE_S, interval_s: float = POLL_S) -> CheckResult:
    """I3: after the same file was uploaded twice, the artefact's chunk count and keys are what they were after the first."""
    if not upload.keys_after_first or upload.second_uploaded_at is None:
        return CheckResult("I3", False, "no keys were recorded after the first upload: nothing to compare with")
    ready = upload.second_uploaded_at + settle_s
    while clock() < ready:
        sleep(min(interval_s, ready - clock()))
    now = [c["chunk_id"] for c in read_chunks(upload.artefact)]
    before = upload.keys_after_first
    if len(now) != len(before):
        return CheckResult("I3", False, f"{len(now)} chunks after the second upload, {len(before)} after the first")
    if sorted(now) != sorted(before) or len(set(now)) != len(now):
        return CheckResult("I3", False, "the same number of chunks, but the keys differ from those after the first upload")
    return CheckResult("I3", True, f"{len(now)} chunks and the same keys {settle_s:.0f} s after the second upload "
                                   "as after the first")


def i4_broken_to_poison(upload: Upload, peek: Callable[[], list[PoisonMessage]], count: Callable[[], int], *,
                        clock: Clock, sleep: Sleep, timeout_s: float = I4_TIMEOUT_S,
                        interval_s: float = POISON_POLL_S) -> CheckResult:
    """I4: a poison message for the broken upload, inserted within 10 minutes of it, and the index gained nothing.

    The queue is always peeked at least once, and the verdict is the matching message's own InsertionTime
    against the upload's time, so it does not depend on when this runs. The upload's blob name is unique,
    so another upload's message never matches. (A message whose reply carried no InsertionTime is judged by
    when it was first seen.)
    """
    if upload.docs_before is None:
        return CheckResult("I4", False, "the upload recorded no document count: nothing to compare the index with")
    delay: float | None = None
    while True:
        matches = [m for m in peek() if blob_of_message(m.text) == upload.blob]
        if matches:
            stamped = [m.inserted_at for m in matches if m.inserted_at is not None]
            delay = (min(stamped) - upload.uploaded_at) if stamped else clock() - upload.uploaded_at
            break
        if clock() - upload.uploaded_at >= timeout_s:
            break
        sleep(interval_s)
    if delay is None:
        return CheckResult("I4", False, f"no poison message for the upload within {timeout_s / 60:.0f} minutes")
    if delay > timeout_s:
        return CheckResult("I4", False, f"the poison message was inserted {delay:.0f} s after the upload, later than "
                                        f"{timeout_s / 60:.0f} minutes")
    after = count()
    if after != upload.docs_before:
        return CheckResult("I4", False, f"a poison message came after {delay:.0f} s, but the index went from "
                                        f"{upload.docs_before} to {after} documents: it gained {after - upload.docs_before} documents")
    return CheckResult("I4", True, f"a poison message for the upload came {delay:.0f} s after it; the index "
                                   f"gained no documents ({upload.docs_before} before and after)")
