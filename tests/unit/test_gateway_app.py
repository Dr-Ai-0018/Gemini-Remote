import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gemini_remote import __version__
from gemini_remote.config.settings import Settings
from gemini_remote.contracts.envelope import ReplayGuard, pack, unpack
from gemini_remote.gateway.app import create_app
from gemini_remote.storage.jobs import JobStore

CONTROL, DEVICE, SIGNING = "c" * 40, "d" * 40, "s" * 40
RESULT = {"text": "done", "documents": [], "truncated": False}
DEVICE_HEADERS = {"authorization": f"Bearer {DEVICE}", "x-device-id": "pc-1"}


@pytest.fixture
def store(tmp_path):
    s = JobStore(tmp_path / "gateway.sqlite3")
    yield s
    s.close()


@pytest.fixture
def client(tmp_path, store):
    settings = Settings(control_token=CONTROL, device_token=DEVICE, signing_key=SIGNING,
                        device_id="pc-1", data_dir=tmp_path)
    app = create_app(settings, store=store, dispatch_interval=0.02)
    with TestClient(app, base_url="http://127.0.0.1:18797") as c:
        yield c


class Relay:
    """Minimal signed Relay client over the test WebSocket."""

    def __init__(self, ws):
        self.ws = ws
        self.guard = ReplayGuard()

    def send(self, msg_type, payload, key=SIGNING):
        self.ws.send_text(pack(msg_type, payload, key))

    def recv(self, msg_type):
        while True:
            msg = unpack(self.ws.receive_text(), SIGNING, self.guard)
            if msg["type"] == msg_type:
                return msg["payload"]


def submit(store, key="k1", task="write a plan"):
    return store.submit(task, key, "task", 600)["job_id"]


def mcp_post(client, body, token=CONTROL):
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    return client.post("/mcp/", json=body, headers=headers)


def test_health_is_public_and_reports_version(client):
    body = client.get("/health").json()
    assert body == {"ok": True, "version": __version__, "relay_connected": False}


@pytest.mark.parametrize("token", [None, "wrong-token-" + "x" * 30])
def test_mcp_requires_the_control_token(client, token):
    response = mcp_post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=token)
    assert response.status_code == 401


def test_mcp_lists_tools_with_the_control_token(client):
    response = mcp_post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 200
    names = {t["name"] for t in response.json()["result"]["tools"]}
    assert names == {"gemini_send", "gemini_status", "gemini_result"}


@pytest.mark.parametrize("headers", [{}, {**DEVICE_HEADERS, "authorization": "Bearer wrong"},
                                     {**DEVICE_HEADERS, "x-device-id": "other"}])
def test_device_socket_rejects_bad_credentials(client, headers):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/device", headers=headers) as ws:
            ws.receive_text()


def test_full_job_flow_over_the_device_socket(client, store):
    job = submit(store)
    with client.websocket_connect("/ws/device", headers=DEVICE_HEADERS) as ws:
        relay = Relay(ws)
        relay.send("hello", {"relay_version": "test"})
        assert relay.recv("hello") == {"gateway_version": __version__}
        command = relay.recv("command")
        assert command["job_id"] == job and command["task"] == "write a plan" and command["mode"] == "task"
        assert client.get("/health").json()["relay_connected"] is True

        relay.send("ack", {"kind": "command", "id": job})
        relay.send("event", {"event_id": "e1", "job_id": job, "kind": "running", "anchor": "turn-1"})
        assert relay.recv("ack") == {"kind": "event", "id": "e1", "status": "running", "applied": True}
        relay.send("event", {"event_id": "e2", "job_id": job, "kind": "succeeded", "result": RESULT})
        assert relay.recv("ack")["status"] == "succeeded"
    assert store.result(job)["result"] == RESULT


def test_unacked_command_is_offered_again_after_reconnect(client, store):
    job = submit(store)
    with client.websocket_connect("/ws/device", headers=DEVICE_HEADERS) as ws:
        assert Relay(ws).recv("command")["job_id"] == job
    with client.websocket_connect("/ws/device", headers=DEVICE_HEADERS) as ws:
        relay = Relay(ws)
        assert relay.recv("command")["job_id"] == job
        relay.send("ack", {"kind": "command", "id": job})
        relay.send("event", {"event_id": "e1", "job_id": job, "kind": "unconfirmed"})
        assert relay.recv("ack")["status"] == "unconfirmed"
    # Unconfirmed is never offered again, even after another reconnect.
    second = submit(store, "k2", task="second")
    with client.websocket_connect("/ws/device", headers=DEVICE_HEADERS) as ws:
        assert Relay(ws).recv("command")["job_id"] == second


def test_bad_events_are_acked_as_rejected_so_the_relay_can_drop_them(client, store):
    job = submit(store)
    with client.websocket_connect("/ws/device", headers=DEVICE_HEADERS) as ws:
        relay = Relay(ws)
        relay.send("event", {"event_id": "e1", "job_id": "missing", "kind": "failed", "error": "x"})
        assert relay.recv("ack")["rejected"] == "job_not_found"
        relay.send("event", {"event_id": "e2", "job_id": job, "kind": "running"})
        assert relay.recv("ack")["rejected"] == "running_requires_anchor"


def test_second_relay_connection_is_refused(client):
    with client.websocket_connect("/ws/device", headers=DEVICE_HEADERS) as ws:
        Relay(ws).send("hello", {})
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/device", headers=DEVICE_HEADERS) as second:
                second.receive_text()


def test_unsigned_or_forged_message_closes_the_socket(client):
    with client.websocket_connect("/ws/device", headers=DEVICE_HEADERS) as ws:
        Relay(ws).send("hello", {}, key="x" * 40)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 1008
