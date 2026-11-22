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
from gemini_remote.storage.relay_journal import RelayJournal

BROWSER = "b" * 40
TEST_KEY = "Z2VtaW5pLXJlbW90ZS10ZXN0LWtleQ=="  # throwaway vector, see test_settings
ORIGIN = "chrome-extension://adjpkjhmnhmgipgnbfjejnfkbhknkbke"


@pytest.fixture
def journal(tmp_path):
    j = RelayJournal(tmp_path / "relay.sqlite3")
    yield j
    j.close()


class Harness:
    def __init__(self, journal):
        self.journal = journal
        self.state = RelayState()
        self.changes = 0
        settings = Settings(browser_token=BROWSER, extension_key=TEST_KEY)
        self.bridge = BrowserBridge(settings, journal, self.state, on_change=self._changed, hello_timeout=0.3)

    def _changed(self):
        self.changes += 1

    def url(self):
        return f"ws://127.0.0.1:{self.port}"

    def client(self, origin=ORIGIN):
        return connect(self.url(), origin=origin, open_timeout=5)

    async def paired(self, version="9.9.9"):
        ws = await self.client()
        await ws.send(json.dumps({"type": "hello", "token": BROWSER, "extension_version": version}))
        assert json.loads(await ws.recv()) == {"type": "welcome", "relay_version": __version__}
        return ws


@pytest.fixture
async def relay(journal):
    h = Harness(journal)
    async with h.bridge.serve(port=0) as server:
        h.port = server.sockets[0].getsockname()[1]
        yield h


def add_command(journal, job_id="j1"):
    journal.record_command({"job_id": job_id, "task": "write a plan", "mode": "task", "deadline_at": 9e12})


async def recv(ws):
    return json.loads(await asyncio.wait_for(ws.recv(), 5))


async def closed_code(ws):
    with pytest.raises(ConnectionClosed) as exc:
        await asyncio.wait_for(ws.recv(), 5)
    return exc.value.rcvd.code


@pytest.mark.parametrize("origin", [None, "https://gemini.google.com", "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"])
async def test_other_origins_are_refused_at_the_handshake(relay, origin):
    with pytest.raises(InvalidStatus) as exc:
        async with relay.client(origin=origin):
            pass
    assert exc.value.response.status_code == 403


@pytest.mark.parametrize("hello", [{"type": "hello", "token": "x" * 40}, {"type": "status", "browser_ready": True},
                                   {"type": "hello"}, [1, 2]])
async def test_wrong_or_missing_token_closes_the_socket(relay, hello):
    async with relay.client() as ws:
        await ws.send(json.dumps(hello))
        assert await closed_code(ws) == 1008
    assert not relay.state.browser_connected


async def test_silent_client_is_dropped(relay):
    async with relay.client() as ws:
        assert await closed_code(ws) == 1008


async def test_pairing_reports_version_and_answers_pings(relay):
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
    await second.send(json.dumps({"type": "hello", "token": BROWSER}))
    assert await closed_code(second) == 1013
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
    assert await closed_code(ws) == 1008
