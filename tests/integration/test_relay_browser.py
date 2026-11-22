"""Extension-facing loopback socket, exercised with a fake extension client."""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from gemini_remote import __version__
from gemini_remote.config.settings import Settings
from gemini_remote.relay.browser import BrowserBridge
from gemini_remote.relay.state import RelayState
from gemini_remote.storage.pairing import PairingStore
from gemini_remote.storage.relay_journal import RelayJournal

ORIGIN = "chrome-extension://" + "a" * 32
OTHER_EXTENSION = "chrome-extension://" + "b" * 32


@pytest.fixture
def journal(tmp_path):
    j = RelayJournal(tmp_path / "relay.sqlite3")
    yield j
    j.close()


@pytest.fixture
def pairing(tmp_path):
    p = PairingStore(tmp_path / "relay.sqlite3")
    yield p
    p.close()


class Harness:
    def __init__(self, journal, pairing):
        self.journal = journal
        self.pairing = pairing
        self.state = RelayState()
        self.changes = 0
        self.token = pairing.redeem(pairing.new_code(), ORIGIN)
        self.bridge = BrowserBridge(Settings(), journal, pairing, self.state, on_change=self._changed,
                                    hello_timeout=0.3)

    def _changed(self):
        self.changes += 1

    def client(self, origin=ORIGIN):
        return connect(f"ws://127.0.0.1:{self.port}", origin=origin, open_timeout=5)

    async def paired(self, version="9.9.9"):
        ws = await self.client()
        await ws.send(json.dumps({"type": "hello", "token": self.token, "extension_version": version}))
        assert json.loads(await ws.recv()) == {"type": "welcome", "relay_version": __version__}
        return ws


@pytest.fixture
async def relay(journal, pairing):
    h = Harness(journal, pairing)
    async with h.bridge.serve(port=0) as server:
        h.port = server.sockets[0].getsockname()[1]
        yield h


def add_command(journal, job_id="j1"):
    journal.record_command({"job_id": job_id, "task": "write a plan", "mode": "task", "deadline_at": 9e12})


async def recv(ws):
    return json.loads(await asyncio.wait_for(ws.recv(), 5))


async def closed(ws):
    with pytest.raises(ConnectionClosed) as exc:
        while True:  # skip anything sent before the close
            await asyncio.wait_for(ws.recv(), 5)
    return exc.value.rcvd.code, exc.value.rcvd.reason


@pytest.mark.parametrize("origin", [None, "https://gemini.google.com", "http://127.0.0.1",
                                    "chrome-extension://not-an-id", "moz-extension://" + "a" * 32])
async def test_non_extension_origins_are_refused_at_the_handshake(relay, origin):
    with pytest.raises(InvalidStatus) as exc:
        async with relay.client(origin=origin):
            pass
    assert exc.value.response.status_code == 403


@pytest.mark.parametrize("first", [{"type": "hello", "token": "x" * 43}, {"type": "status", "browser_ready": True},
                                   {"type": "hello"}, [1, 2]])
async def test_wrong_or_missing_token_closes_the_socket(relay, first):
    async with relay.client() as ws:
        await ws.send(json.dumps(first))
        assert (await closed(ws))[0] == 1008
    assert not relay.state.browser_connected


async def test_token_only_works_from_the_extension_that_paired(relay):
    async with relay.client(origin=OTHER_EXTENSION) as ws:
        await ws.send(json.dumps({"type": "hello", "token": relay.token}))
        assert await closed(ws) == (1008, "unauthorized")


async def test_silent_client_is_dropped(relay):
    async with relay.client() as ws:
        assert await closed(ws) == (1008, "hello_required")


async def test_pairing_with_a_code_hands_out_a_token_that_works_next_time(relay, pairing):
    code = pairing.new_code()
    async with relay.client(origin=OTHER_EXTENSION) as ws:
        await ws.send(json.dumps({"type": "pair", "code": code, "extension_version": "0.1.4"}))
        paired = await recv(ws)
        assert paired["type"] == "paired" and len(paired["token"]) >= 32
        assert await recv(ws) == {"type": "welcome", "relay_version": __version__}
        assert relay.state.extension_version == "0.1.4"
    assert not pairing.check(relay.token, ORIGIN)  # the old pairing is gone
    async with relay.client(origin=OTHER_EXTENSION) as again:
        await again.send(json.dumps({"type": "hello", "token": paired["token"]}))
        assert (await recv(again))["type"] == "welcome"


async def test_codes_are_single_use(relay, pairing):
    code = pairing.new_code()
    async with relay.client() as ws:
        await ws.send(json.dumps({"type": "pair", "code": code}))
        assert (await recv(ws))["type"] == "paired"
    async with relay.client() as ws:
        await ws.send(json.dumps({"type": "pair", "code": code}))
        assert await closed(ws) == (1008, "no_code")


async def test_wrong_code_is_refused_and_the_pairing_is_kept(relay, pairing):
    code = pairing.new_code()
    async with relay.client() as ws:
        await ws.send(json.dumps({"type": "pair", "code": "000000" if code != "000000" else "111111"}))
        assert await closed(ws) == (1008, "bad_code")
    assert pairing.check(relay.token, ORIGIN)


async def test_new_pairing_disconnects_the_old_extension(relay, pairing):
    old = await relay.paired()
    code = pairing.new_code()
    async with relay.client(origin=OTHER_EXTENSION) as new:
        await new.send(json.dumps({"type": "pair", "code": code}))
        assert (await recv(new))["type"] == "paired"
        assert await closed(old) == (1008, "unauthorized")
        assert (await recv(new))["type"] == "welcome"
        await new.send(json.dumps({"type": "ping"}))
        assert await recv(new) == {"type": "pong"}
        assert relay.state.browser_connected


async def test_unpairing_from_another_process_is_noticed_on_the_next_ping(relay, tmp_path):
    ws = await relay.paired()
    cli = PairingStore(tmp_path / "relay.sqlite3")  # what `gemini-remote pair --reset` does
    cli.reset()
    cli.close()
    await ws.send(json.dumps({"type": "ping"}))
    assert await closed(ws) == (1008, "unauthorized")


async def test_hello_reports_version_and_answers_pings(relay):
    ws = await relay.paired("0.1.3")
    assert relay.state.browser_connected and relay.state.extension_version == "0.1.3"
    await ws.send(json.dumps({"type": "ping"}))
    assert await recv(ws) == {"type": "pong"}
    await ws.close()
    await asyncio.sleep(0.05)
    assert not relay.state.browser_connected and not relay.state.browser_ready


async def test_only_one_extension_at_a_time(relay):
    first = await relay.paired()
    second = await relay.client()
    await second.send(json.dumps({"type": "hello", "token": relay.token}))
    assert await closed(second) == (1013, "busy")
    await first.send(json.dumps({"type": "ping"}))
    assert await recv(first) == {"type": "pong"}
    await first.close()


async def test_command_waits_for_ready_and_is_handed_over_once(relay, journal):
    add_command(journal)
    ws = await relay.paired()
    await relay.bridge.offer()  # not ready yet: nothing is sent
    assert journal.command_state("j1") == "pending"

    await ws.send(json.dumps({"type": "status", "browser_ready": True}))
    command = await recv(ws)
    assert command == {"type": "command", "job_id": "j1", "task": "write a plan", "mode": "task",
                       "deadline_at": 9e12}
    assert journal.command_state("j1") == "delivered"
    await ws.close()

    again = await relay.paired()  # reconnect: the same command is not sent again
    await again.send(json.dumps({"type": "status", "browser_ready": True}))
    await again.send(json.dumps({"type": "ping"}))
    assert await recv(again) == {"type": "pong"}
    await again.close()


async def test_new_command_is_pushed_to_a_ready_extension(relay, journal):
    ws = await relay.paired()
    await ws.send(json.dumps({"type": "status", "browser_ready": True}))
    await ws.send(json.dumps({"type": "ping"}))
    assert await recv(ws) == {"type": "pong"}
    add_command(journal, "j2")
    await relay.bridge.offer()  # what the upstream client calls after journaling a command
    assert (await recv(ws))["job_id"] == "j2"
    await ws.close()


async def test_events_are_queued_for_the_gateway_and_acked(relay, journal):
    add_command(journal)
    journal.mark_delivered("j1")
    ws = await relay.paired()
    event = {"event_id": "e1", "job_id": "j1", "kind": "running", "anchor": "turn-1"}
    before = relay.changes
    await ws.send(json.dumps({"type": "event", "event": event}))
    assert await recv(ws) == {"type": "ack", "event_id": "e1", "accepted": True}
    assert relay.changes > before
    await ws.send(json.dumps({"type": "event", "event": event}))  # re-sent after a lost ack
    assert await recv(ws) == {"type": "ack", "event_id": "e1", "accepted": True}
    assert journal.unacked_events() == [event]

    await ws.send(json.dumps({"type": "event", "event": {**event, "event_id": "e2", "job_id": "nope"}}))
    assert await recv(ws) == {"type": "ack", "event_id": "e2", "rejected": "unknown_job"}
    await ws.close()


async def test_unknown_message_closes_the_socket(relay):
    ws = await relay.paired()
    await ws.send(json.dumps({"type": "click", "selector": "button"}))
    assert (await closed(ws))[0] == 1008
