import json

import pytest

from gemini_remote.contracts.envelope import (
    MAX_MESSAGE_BYTES,
    EnvelopeError,
    ReplayGuard,
    pack,
    unpack,
)

KEY = "k" * 40
NOW = 1_800_000_000.0


def guard():
    return ReplayGuard(clock=lambda: NOW)


def test_round_trip_keeps_payload_and_type():
    text = pack("event", {"job_id": "j1", "text": "中文"}, KEY, now=NOW)
    msg = unpack(text, KEY, guard(), now=NOW)
    assert msg["type"] == "event"
    assert msg["payload"] == {"job_id": "j1", "text": "中文"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m["payload"].__setitem__("job_id", "j2"),
        lambda m: m.__setitem__("type", "command"),
        lambda m: m.__setitem__("ts", m["ts"] + 1),
    ],
)
def test_any_tampering_breaks_the_signature(mutate):
    msg = json.loads(pack("event", {"job_id": "j1"}, KEY, now=NOW))
    mutate(msg)
    with pytest.raises(EnvelopeError, match="bad_signature"):
        unpack(json.dumps(msg), KEY, guard(), now=NOW)


def test_wrong_key_is_rejected():
    with pytest.raises(EnvelopeError, match="bad_signature"):
        unpack(pack("event", {}, KEY, now=NOW), "x" * 40, guard(), now=NOW)


@pytest.mark.parametrize("offset", [-61, 61])
def test_messages_outside_the_clock_window_are_rejected(offset):
    text = pack("event", {}, KEY, now=NOW + offset)
    with pytest.raises(EnvelopeError, match="stale"):
        unpack(text, KEY, guard(), now=NOW)


def test_replayed_message_is_rejected_once_accepted():
    g = guard()
    text = pack("event", {}, KEY, now=NOW)
    unpack(text, KEY, g, now=NOW)
    with pytest.raises(EnvelopeError, match="replayed"):
        unpack(text, KEY, g, now=NOW)


def test_rejected_message_does_not_burn_its_id():
    g = guard()
    good = pack("event", {}, KEY, msg_id="m1", now=NOW)
    forged = json.loads(good)
    forged["signature"] = "0" * 64
    with pytest.raises(EnvelopeError):
        unpack(json.dumps(forged), KEY, g, now=NOW)
    unpack(good, KEY, g, now=NOW)


def test_oversized_messages_are_refused_both_ways():
    with pytest.raises(EnvelopeError, match="message_too_large"):
        pack("event", {"text": "x" * MAX_MESSAGE_BYTES}, KEY, now=NOW)
    with pytest.raises(EnvelopeError, match="message_too_large"):
        unpack("x" * (MAX_MESSAGE_BYTES + 1), KEY, guard(), now=NOW)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        json.dumps({"v": 1, "id": "a", "type": "t", "ts": 1, "payload": {}}),
        json.dumps({"v": 1, "id": "a", "type": "t", "ts": True, "payload": {}, "signature": ""}),
        json.dumps({"v": 1, "id": "", "type": "t", "ts": 1, "payload": {}, "signature": ""}),
        json.dumps({"v": 1, "id": "a", "type": "t", "ts": 1, "payload": [], "signature": ""}),
    ],
)
def test_malformed_messages_are_rejected(text):
    with pytest.raises(EnvelopeError, match="malformed"):
        unpack(text, KEY, guard(), now=NOW)


def test_unknown_version_is_rejected():
    msg = json.loads(pack("event", {}, KEY, now=NOW))
    msg["v"] = 2
    with pytest.raises(EnvelopeError, match="unsupported_version"):
        unpack(json.dumps(msg), KEY, guard(), now=NOW)
