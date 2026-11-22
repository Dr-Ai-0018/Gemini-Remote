"""Relay -> Gateway WebSocket client.

Dials the Gateway (the Relay never listens on the network), authenticates with
the device token, and speaks the signed device protocol described in
gemini_remote.gateway.app:

- every command is written to the journal before it is acked;
- after (re)connecting it sends hello, a heartbeat, then every outbox event
  the Gateway has not acked yet;
- on any failure it reconnects with exponential backoff and jitter.
"""

from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any
import asyncio
import logging
import random

from websockets.asyncio.client import ClientConnection, connect

from gemini_remote import __version__
from gemini_remote.config.settings import Settings
from gemini_remote.contracts.envelope import MAX_MESSAGE_BYTES, EnvelopeError, ReplayGuard, pack, unpack
from gemini_remote.relay.state import RelayState
from gemini_remote.storage.relay_journal import RelayJournal

log = logging.getLogger("gemini_remote.relay.upstream")


class Upstream:
    def __init__(self, settings: Settings, journal: RelayJournal, state: RelayState, *,
                 on_command: Callable[[], Awaitable[None]] | None = None,
                 heartbeat_interval: float = 15.0, backoff_initial: float = 1.0,
                 backoff_max: float = 30.0):
        self.settings = settings
        self.journal = journal
        self.state = state
        self.on_command = on_command
        self.heartbeat_interval = heartbeat_interval
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """New outbox event or browser state change: send it now instead of at the next heartbeat."""
        self._wake.set()

    async def run(self) -> None:
        delay = self.backoff_initial
        headers = {"authorization": f"Bearer {self.settings.device_token}",
                   "x-device-id": self.settings.device_id}
        while True:
            try:
                async with connect(self.settings.gateway_url, additional_headers=headers,
                                   max_size=MAX_MESSAGE_BYTES, open_timeout=10) as ws:
                    delay = self.backoff_initial
                    self.state.gateway_connected = True
                    log.info("connected to gateway")
                    await self._session(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never log message content
                log.warning("gateway connection lost: %s", type(exc).__name__)
            finally:
                if self.state.gateway_connected:
                    log.info("disconnected from gateway")
                self.state.gateway_connected = False
                self.state.gateway_version = None
            await asyncio.sleep(delay * random.uniform(0.5, 1.0))
            delay = min(delay * 2, self.backoff_max)

    async def _session(self, ws: ClientConnection) -> None:
        guard = ReplayGuard()
        lock = asyncio.Lock()
        inflight: set[str] = set()

        async def send(msg_type: str, payload: dict[str, Any]) -> None:
            text = pack(msg_type, payload, self.settings.signing_key)
            async with lock:
                await ws.send(text)

        async def reader() -> None:
            async for raw in ws:
                msg = unpack(raw, self.settings.signing_key, guard)
                payload = msg["payload"]
                match msg["type"]:
                    case "hello":
                        version = payload.get("gateway_version")
                        self.state.gateway_version = version[:64] if isinstance(version, str) else None
                    case "command":
                        try:
                            self.journal.record_command(payload)
                        except ValueError:
                            log.warning("ignored invalid command")
                            continue
                        await send("ack", {"kind": "command", "id": payload["job_id"]})
                        if self.on_command:
                            await self.on_command()
                    case "ack" if payload.get("kind") == "event" and isinstance(payload.get("id"), str):
                        if "rejected" in payload:
                            log.warning("gateway rejected event: %s", str(payload["rejected"])[:64])
                        self.journal.mark_event_acked(payload["id"])
                        inflight.discard(payload["id"])
                    case _:
                        raise EnvelopeError("unexpected_message")

        async def writer() -> None:
            while True:
                self._wake.clear()
                await send("heartbeat", {"browser_ready": self.state.browser_connected and self.state.browser_ready,
                                         "extension_version": self.state.extension_version})
                for event in self.journal.unacked_events():
                    if event["event_id"] not in inflight:
                        inflight.add(event["event_id"])
                        await send("event", event)
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), self.heartbeat_interval)

        await send("hello", {"relay_version": __version__})
        tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in done:
            task.result()  # surface the error that ended the session
