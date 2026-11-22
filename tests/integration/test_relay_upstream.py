"""Relay upstream client against the real Gateway app, served by uvicorn on loopback."""

import asyncio

import pytest
from loopback import CONTROL, DEVICE, RESULT, SIGNING, Gateway, until

from gemini_remote import __version__
from gemini_remote.config.settings import Settings
from gemini_remote.relay.state import RelayState
from gemini_remote.relay.upstream import Upstream
from gemini_remote.storage.jobs import JobStore
from gemini_remote.storage.relay_journal import RelayJournal


@pytest.fixture
def store(tmp_path):
    s = JobStore(tmp_path / "gateway.sqlite3")
    yield s
    s.close()


@pytest.fixture
def journal(tmp_path):
    j = RelayJournal(tmp_path / "relay.sqlite3")
    yield j
    j.close()


@pytest.fixture
async def gateway(tmp_path, store):
    g = Gateway(Settings(control_token=CONTROL, device_token=DEVICE, signing_key=SIGNING,
                         device_id="pc-1", data_dir=tmp_path), store)
    await g.start()
    yield g
    if not g.task.done():
        await g.stop()


class Running:
    """An Upstream running in the background for the duration of a test."""

    def __init__(self, gateway, journal, token=DEVICE):
        self.state = RelayState()
        self.commands = 0
        settings = Settings(device_token=token, signing_key=SIGNING, device_id="pc-1",
                            gateway_url=f"ws://127.0.0.1:{gateway.port}/ws/device")
        self.upstream = Upstream(settings, journal, self.state, on_command=self._on_command,
                                 heartbeat_interval=0.05, backoff_initial=0.02, backoff_max=0.1)

    async def _on_command(self):
        self.commands += 1

    async def __aenter__(self):
        self.task = asyncio.create_task(self.upstream.run())
        return self

    async def __aexit__(self, *exc):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


def submit(store, key="k1"):
    return store.submit("write a plan", key, "task", 600)["job_id"]


def applied_events(store, job_id):
    return store.db.execute("SELECT id FROM events WHERE job_id=? ORDER BY received_at", (job_id,)).fetchall()


async def test_command_is_journaled_then_acked_and_events_flow_back(gateway, store, journal):
    job = submit(store)
    async with Running(gateway, journal) as relay:
        await until(lambda: store.status(job)["status"] == "dispatched")
        assert journal.command_state(job) == "pending"
        assert relay.commands == 1
        assert relay.state.gateway_connected and relay.state.gateway_version == __version__

        journal.mark_delivered(job)
        journal.add_event({"event_id": "e1", "job_id": job, "kind": "running", "anchor": "turn-1"})
        relay.upstream.wake()
        await until(lambda: store.status(job)["status"] == "running")
        await until(lambda: journal.unacked_events() == [])


async def test_events_queued_while_the_gateway_is_down_arrive_once_after_restart(gateway, store, journal):
    job = submit(store)
    async with Running(gateway, journal) as relay:
        await until(lambda: store.status(job)["status"] == "dispatched")
        await gateway.stop()
        await until(lambda: not relay.state.gateway_connected)

        journal.mark_delivered(job)
        journal.add_event({"event_id": "e1", "job_id": job, "kind": "running", "anchor": "turn-1"})
        journal.add_event({"event_id": "e2", "job_id": job, "kind": "succeeded", "result": RESULT})
        relay.upstream.wake()

        await gateway.start()  # same port, same store
        await until(lambda: store.status(job)["status"] == "succeeded")
        await until(lambda: journal.unacked_events() == [])
    assert store.result(job)["result"] == RESULT
    assert [r["id"] for r in applied_events(store, job)] == ["e1", "e2"]


async def test_event_whose_ack_was_lost_is_resent_but_applied_once(gateway, store, journal):
    job = submit(store)
    async with Running(gateway, journal) as relay:
        await until(lambda: store.status(job)["status"] == "dispatched")
        journal.mark_delivered(job)
        event = {"event_id": "e1", "job_id": job, "kind": "running", "anchor": "turn-1"}
        store.apply_event(event)  # the Gateway already has it; only the ack never came back
        journal.add_event(event)
        relay.upstream.wake()
        await until(lambda: journal.unacked_events() == [])
    assert store.status(job)["status"] == "running"
    assert len(applied_events(store, job)) == 1


async def test_command_reoffered_after_a_relay_crash_is_not_handed_over_again(gateway, store, journal):
    job = submit(store)
    command = {"job_id": job, "task": "write a plan", "mode": "task", "deadline_at": store.status(job)["deadline_at"]}
    # Before the crash: journaled and handed to the browser, but the ack never reached the Gateway.
    journal.record_command(command)
    journal.mark_delivered(job)
    async with Running(gateway, journal):
        await until(lambda: store.status(job)["status"] == "dispatched")
    assert journal.command_state(job) == "delivered"
    assert journal.next_pending() is None


async def test_wrong_device_token_never_connects(gateway, store, journal):
    async with Running(gateway, journal, token="x" * 40) as relay:
        await asyncio.sleep(0.3)
        assert not relay.state.gateway_connected
