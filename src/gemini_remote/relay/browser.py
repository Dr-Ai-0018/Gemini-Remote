"""Loopback WebSocket for the browser extension (127.0.0.1 only).

Access control:
- the handshake must carry the Origin of our own extension (its ID is pinned by
  GEMINI_REMOTE_EXTENSION_KEY); anything else gets HTTP 403;
- the first message must be hello with the browser token, within a few seconds;
- only one extension connection at a time; a second one is closed.

Protocol (plain JSON; the socket never leaves this machine):
  extension -> Relay: {"type": "hello", "token", "extension_version"}
                      {"type": "status", "browser_ready": bool}
                      {"type": "event", "event": {event_id, job_id, kind, anchor?, result?, error?}}
                      {"type": "ping"}
  Relay -> extension: {"type": "welcome", "relay_version"}
                      {"type": "command", "job_id", "task", "mode", "deadline_at"}
                      {"type": "ack", "event_id", "accepted": true} | {"type": "ack", "event_id", "rejected": code}
                      {"type": "pong"}
A command is sent only while the extension reports browser_ready, and each
command is sent at most once (see storage.relay_journal).
"""

from collections.abc import Callable
from http import HTTPStatus
from typing import Any
import asyncio
import hmac
import json
import logging

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from gemini_remote import __version__
from gemini_remote.config.settings import Settings
from gemini_remote.contracts.envelope import MAX_MESSAGE_BYTES
from gemini_remote.relay.state import RelayState
from gemini_remote.storage.relay_journal import RelayJournal

log = logging.getLogger("gemini_remote.relay.browser")
POLICY_VIOLATION = 1008
TRY_AGAIN_LATER = 1013


class ProtocolError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _short(value: Any) -> str | None:
    return value[:64] if isinstance(value, str) else None


class BrowserBridge:
    def __init__(self, settings: Settings, journal: RelayJournal, state: RelayState, *,
                 on_change: Callable[[], None] = lambda: None, hello_timeout: float = 5.0):
        self.settings = settings
        self.journal = journal
        self.state = state
        self.on_change = on_change
        self.hello_timeout = hello_timeout
        self.origin = settings.extension_origin
        self._conn: ServerConnection | None = None
        self._offer_lock = asyncio.Lock()

    def serve(self, host: str = "127.0.0.1", port: int | None = None) -> Any:
        """Async context manager / awaitable that starts the loopback server."""
        return serve(self._handler, host, self.settings.relay_port if port is None else port,
                     process_request=self._check_origin, max_size=MAX_MESSAGE_BYTES)

    def _check_origin(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.headers.get("Origin") != self.origin:
            log.warning("refused browser connection from another origin")
            return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
        return None

    async def _hello(self, conn: ServerConnection) -> dict[str, Any]:
        try:
            msg = json.loads(await asyncio.wait_for(conn.recv(), self.hello_timeout))
        except (TimeoutError, ValueError, TypeError):
            raise ProtocolError("hello_required") from None
        if not isinstance(msg, dict):
            raise ProtocolError("hello_required")
        token = msg.get("token")
        if (msg.get("type") != "hello" or not isinstance(token, str)
                or not hmac.compare_digest(token.encode(), self.settings.browser_token.encode())):
            raise ProtocolError("unauthorized")
        return msg

    async def _handler(self, conn: ServerConnection) -> None:
        try:
            hello = await self._hello(conn)
        except ProtocolError as exc:
            log.warning("browser connection refused: %s", exc.code)
            await conn.close(POLICY_VIOLATION, exc.code)
            return
        except ConnectionClosed:
            return
        if self._conn is not None:
            await conn.close(TRY_AGAIN_LATER, "busy")
            return
        self._conn = conn
        self.state.browser_connected = True
        self.state.browser_ready = False
        self.state.extension_version = _short(hello.get("extension_version"))
        log.info("extension connected (version %s)", self.state.extension_version)
        self.on_change()
        try:
            await conn.send(json.dumps({"type": "welcome", "relay_version": __version__}))
            async for raw in conn:
                await self._handle(conn, raw)
        except ProtocolError as exc:
            log.warning("browser message rejected: %s", exc.code)
            await conn.close(POLICY_VIOLATION, exc.code)
        except ConnectionClosed:
            pass
        finally:
            self._conn = None
            self.state.browser_connected = False
            self.state.browser_ready = False
            log.info("extension disconnected")
            self.on_change()

    async def _handle(self, conn: ServerConnection, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            raise ProtocolError("malformed") from None
        if not isinstance(msg, dict):
            raise ProtocolError("malformed")
        match msg.get("type"):
            case "ping":
                await conn.send(json.dumps({"type": "pong"}))
            case "status" if isinstance(msg.get("browser_ready"), bool):
                self.state.browser_ready = msg["browser_ready"]
                self.on_change()
                await self.offer()
            case "event" if isinstance(msg.get("event"), dict):
                event = msg["event"]
                reply: dict[str, Any] = {"type": "ack", "event_id": _short(event.get("event_id"))}
                try:
                    self.journal.add_event(event)
                    reply["accepted"] = True
                    self.on_change()
                except ValueError as exc:
                    reply["rejected"] = str(exc)[:64]
                await conn.send(json.dumps(reply))
            case _:
                raise ProtocolError("unexpected_message")

    async def offer(self) -> None:
        """Hand pending commands to the extension, each at most once."""
        async with self._offer_lock:
            conn = self._conn
            while conn is not None and self.state.browser_ready:
                command = self.journal.next_pending()
                if command is None or not self.journal.mark_delivered(command["job_id"]):
                    return
                try:
                    await conn.send(json.dumps({"type": "command", **command}))
                except ConnectionClosed:
                    # Already claimed: it stays delivered and is not re-sent (the Gateway times it out).
                    log.warning("extension went away while a command was being handed over")
                    return
                log.info("command handed to the extension")
