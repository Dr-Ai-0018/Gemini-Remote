"""Signed envelopes for every Gateway <-> Relay WebSocket message.

Wire format: {"v":1,"id":...,"type":...,"ts":...,"payload":{...},"signature":...}.
The signature is HMAC-SHA256 (hex) over the canonical JSON of every other
field: sorted keys, compact separators, UTF-8. A message is accepted only once,
only inside the clock window, and only below the size limit.
"""

from collections.abc import Callable
from typing import Any
import hashlib
import hmac
import json
import time
import uuid

VERSION = 1
MAX_MESSAGE_BYTES = 256 * 1024
MAX_RESULT_BYTES = 128 * 1024
CLOCK_SKEW_SECONDS = 60
MAX_ID_LENGTH = 128
MAX_TYPE_LENGTH = 64


class EnvelopeError(ValueError):
    """Rejected message. The reason is a short code, never message content."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sign(body: dict[str, Any], key: str) -> str:
    return hmac.new(key.encode("utf-8"), canonical(body), hashlib.sha256).hexdigest()


def pack(
    msg_type: str,
    payload: dict[str, Any],
    key: str,
    *,
    msg_id: str | None = None,
    now: float | None = None,
) -> str:
    body = {
        "v": VERSION,
        "id": msg_id or uuid.uuid4().hex,
        "type": msg_type,
        "ts": int(time.time() if now is None else now),
        "payload": payload,
    }
    text = json.dumps({**body, "signature": _sign(body, key)}, separators=(",", ":"), ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise EnvelopeError("message_too_large")
    return text


class ReplayGuard:
    """Remembers accepted message IDs for as long as their timestamps stay valid."""

    def __init__(self, window: int = CLOCK_SKEW_SECONDS, clock: Callable[[], float] = time.time):
        self._window = window
        self._clock = clock
        self._seen: dict[str, int] = {}

    def accept(self, msg_id: str, ts: int) -> None:
        now = self._clock()
        for old_id, old_ts in list(self._seen.items()):
            if old_ts < now - 2 * self._window:
                del self._seen[old_id]
        if msg_id in self._seen:
            raise EnvelopeError("replayed")
        self._seen[msg_id] = ts


def unpack(text: str | bytes, key: str, guard: ReplayGuard, *, now: float | None = None) -> dict[str, Any]:
    raw = text.encode("utf-8") if isinstance(text, str) else text
    if len(raw) > MAX_MESSAGE_BYTES:
        raise EnvelopeError("message_too_large")
    try:
        msg = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvelopeError("malformed") from exc
    if not isinstance(msg, dict) or set(msg) != {"v", "id", "type", "ts", "payload", "signature"}:
        raise EnvelopeError("malformed")
    if msg["v"] != VERSION:
        raise EnvelopeError("unsupported_version")
    if (not isinstance(msg["id"], str) or not 0 < len(msg["id"]) <= MAX_ID_LENGTH
            or not isinstance(msg["type"], str) or not 0 < len(msg["type"]) <= MAX_TYPE_LENGTH
            or not isinstance(msg["ts"], int) or isinstance(msg["ts"], bool)
            or not isinstance(msg["payload"], dict) or not isinstance(msg["signature"], str)):
        raise EnvelopeError("malformed")
    body = {k: msg[k] for k in ("v", "id", "type", "ts", "payload")}
    if not hmac.compare_digest(msg["signature"], _sign(body, key)):
        raise EnvelopeError("bad_signature")
    current = time.time() if now is None else now
    if abs(current - msg["ts"]) > CLOCK_SKEW_SECONDS:
        raise EnvelopeError("stale")
    guard.accept(msg["id"], msg["ts"])
    return body
