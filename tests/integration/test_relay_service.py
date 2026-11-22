"""Whole chain on loopback: Gateway <-> Relay process <-> fake extension."""

import asyncio
import json
import socket

from loopback import CONTROL, DEVICE, RESULT, SIGNING, Gateway, until
from websockets.asyncio.client import connect

from gemini_remote.config.settings import Settings
from gemini_remote.relay.service import run_relay
from gemini_remote.relay.state import read_status
from gemini_remote.storage.jobs import JobStore

BROWSER = "b" * 40
TEST_KEY = "Z2VtaW5pLXJlbW90ZS10ZXN0LWtleQ=="
ORIGIN = "chrome-extension://adjpkjhmnhmgipgnbfjejnfkbhknkbke"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_job_travels_from_gateway_to_extension_and_back(tmp_path):
    store = JobStore(tmp_path / "gateway.sqlite3")
    gateway = Gateway(Settings(control_token=CONTROL, device_token=DEVICE, signing_key=SIGNING,
                               device_id="pc-1", data_dir=tmp_path / "gw"), store)
    await gateway.start()
    relay_dir = tmp_path / "relay"
    settings = Settings(device_token=DEVICE, signing_key=SIGNING, browser_token=BROWSER, device_id="pc-1",
                        gateway_url=f"ws://127.0.0.1:{gateway.port}/ws/device", relay_port=free_port(),
                        data_dir=relay_dir, extension_key=TEST_KEY)
    relay = asyncio.create_task(run_relay(settings, status_interval=0.05))
    try:
        await until(lambda: (read_status(relay_dir) or {}).get("gateway_connected"))
        job = store.submit("write a plan", "k1", "task", 600)["job_id"]

        async with connect(f"ws://127.0.0.1:{settings.relay_port}", origin=ORIGIN) as ext:
            await ext.send(json.dumps({"type": "hello", "token": BROWSER, "extension_version": "0.1.3"}))
            assert json.loads(await ext.recv())["type"] == "welcome"
            await ext.send(json.dumps({"type": "status", "browser_ready": True}))
            command = json.loads(await asyncio.wait_for(ext.recv(), 5))
            assert command["type"] == "command" and command["job_id"] == job
            assert store.status(job)["status"] == "dispatched"

            for event in ({"event_id": "e1", "job_id": job, "kind": "running", "anchor": "turn-1"},
                          {"event_id": "e2", "job_id": job, "kind": "succeeded", "result": RESULT}):
                await ext.send(json.dumps({"type": "event", "event": event}))
                assert json.loads(await ext.recv())["accepted"] is True
            await until(lambda: store.status(job)["status"] == "succeeded")
            await until(lambda: (read_status(relay_dir) or {}).get("outbox") == 0)  # acked by the Gateway
            status = read_status(relay_dir)
            assert status["browser_ready"] is True and status["extension_version"] == "0.1.3"
        assert store.result(job)["result"] == RESULT
    finally:
        relay.cancel()
        await asyncio.gather(relay, return_exceptions=True)
        await gateway.stop()
        store.close()
    assert read_status(relay_dir) is None  # removed on shutdown, so doctor reports "not running"
