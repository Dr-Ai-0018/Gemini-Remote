"""Gateway: MCP endpoint, device WebSocket for the Relay, and the dispatch loop.

Device protocol (all messages are signed envelopes):
  Relay -> Gateway: hello {relay_version}, heartbeat {browser_ready, extension_version},
                    ack {kind: "command", id: job_id}, event {event_id, job_id, kind, ...}
  Gateway -> Relay: hello {gateway_version}, command {job_id, task, mode, deadline_at},
                    ack {kind: "event", id: event_id, status | rejected}
A command counts as dispatched only after the Relay acks it, i.e. after it is
on the Relay's disk. Un-acked commands are offered again after a reconnect;
the Relay de-duplicates by job_id.
"""

from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any
import asyncio
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from gemini_remote import __version__
from gemini_remote.config.settings import Settings
from gemini_remote.contracts.envelope import EnvelopeError, ReplayGuard, pack, unpack
from gemini_remote.gateway.auth import BearerGuard, bearer_matches
from gemini_remote.gateway.mcp import create_mcp
from gemini_remote.storage.jobs import JobNotFound, JobStore

log = logging.getLogger("gemini_remote.gateway")
POLICY_VIOLATION = 1008


@dataclass
class DeviceSession:
    ws: WebSocket
    signing_key: str
    guard: ReplayGuard = field(default_factory=ReplayGuard)
    offered: set[str] = field(default_factory=set)
    relay_version: str | None = None
    extension_version: str | None = None
    browser_ready: bool | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, msg_type: str, payload: dict[str, Any]) -> None:
        async with self.lock:
            await self.ws.send_text(pack(msg_type, payload, self.signing_key))


def _short(value: Any) -> str | None:
    return value[:64] if isinstance(value, str) else None


def create_app(settings: Settings, *, store: JobStore | None = None,
               dispatch_interval: float = 0.25) -> FastAPI:
    owns_store = store is None
    store = store or JobStore(settings.data_dir / "gateway.sqlite3")
    mcp = create_mcp(store, settings.default_mode, ["127.0.0.1:*", "localhost:*"])
    mcp_app = mcp.streamable_http_app()
    state: dict[str, DeviceSession | None] = {"session": None}

    async def dispatch_once() -> None:
        store.expire()
        session = state["session"]
        if session is None:
            return
        command = store.next_command()
        if command and command["job_id"] not in session.offered:
            session.offered.add(command["job_id"])
            await session.send("command", command)

    async def dispatch_loop() -> None:
        while True:
            try:
                await dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep dispatching; never log job content
                log.warning("dispatch error: %s", type(exc).__name__)
            await asyncio.sleep(dispatch_interval)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            task = asyncio.create_task(dispatch_loop())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                if owns_store:
                    store.close()

    app = FastAPI(title="Gemini Remote Gateway", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.store = store
    app.state.device = state

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "relay_connected": state["session"] is not None}

    async def handle(session: DeviceSession, msg: dict[str, Any]) -> None:
        payload = msg["payload"]
        match msg["type"]:
            case "hello":
                session.relay_version = _short(payload.get("relay_version"))
                await session.send("hello", {"gateway_version": __version__})
            case "heartbeat":
                ready = payload.get("browser_ready")
                session.browser_ready = ready if isinstance(ready, bool) else None
                session.extension_version = _short(payload.get("extension_version"))
            case "ack" if payload.get("kind") == "command" and isinstance(payload.get("id"), str):
                with suppress(JobNotFound):
                    store.ack_command(payload["id"])
            case "event" if isinstance(payload.get("event_id"), str):
                reply: dict[str, Any] = {"kind": "event", "id": payload["event_id"]}
                try:
                    outcome = store.apply_event(payload)
                    reply.update(status=outcome["status"], applied=outcome["applied"])
                except JobNotFound:
                    reply["rejected"] = "job_not_found"
                except ValueError as exc:
                    # Acked as rejected so the Relay outbox does not retry it forever.
                    reply["rejected"] = str(exc)[:64]
                    log.warning("rejected event: %s", reply["rejected"])
                await session.send("ack", reply)
            case _:
                raise EnvelopeError("unexpected_message")

    @app.websocket("/ws/device")
    async def device(ws: WebSocket) -> None:
        if (ws.headers.get("x-device-id") != settings.device_id
                or not bearer_matches(ws.headers.get("authorization"), settings.device_token)):
            await ws.close(code=POLICY_VIOLATION)
            return
        if state["session"] is not None:
            await ws.close(code=POLICY_VIOLATION)
            return
        await ws.accept()
        session = DeviceSession(ws, settings.signing_key)
        state["session"] = session
        log.info("relay connected")
        try:
            while True:
                await handle(session, unpack(await ws.receive_text(), settings.signing_key, session.guard))
        except WebSocketDisconnect:
            pass
        except EnvelopeError as exc:
            log.warning("relay message rejected: %s", exc)
            with suppress(Exception):
                await ws.close(code=POLICY_VIOLATION)
        finally:
            if state["session"] is session:
                state["session"] = None
            log.info("relay disconnected")

    app.mount("/mcp", BearerGuard(mcp_app, settings.control_token))
    return app
