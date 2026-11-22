"""The Relay process: browser socket on loopback + upstream client to the Gateway."""

from contextlib import suppress
import asyncio
import logging

from gemini_remote import __version__
from gemini_remote.config.settings import Settings
from gemini_remote.relay.browser import BrowserBridge
from gemini_remote.relay.state import STATUS_FILE, RelayState
from gemini_remote.relay.upstream import Upstream
from gemini_remote.storage.relay_journal import RelayJournal

log = logging.getLogger("gemini_remote.relay")
JOURNAL_FILE = "relay.sqlite3"


async def run_relay(settings: Settings, *, status_interval: float = 5.0) -> None:
    journal = RelayJournal(settings.data_dir / JOURNAL_FILE)
    state = RelayState()

    def write_status() -> None:
        try:
            state.write(settings.data_dir, __version__, journal.counts())
        except OSError as exc:
            log.warning("could not write relay status: %s", type(exc).__name__)

    def changed() -> None:
        upstream.wake()
        write_status()

    bridge = BrowserBridge(settings, journal, state, on_change=changed)
    upstream = Upstream(settings, journal, state, on_command=bridge.offer)
    try:
        async with bridge.serve():
            log.info("relay %s listening for the extension on 127.0.0.1:%d", __version__, settings.relay_port)
            task = asyncio.create_task(upstream.run())
            try:
                while True:
                    write_status()
                    await asyncio.sleep(status_interval)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        with suppress(OSError):
            (settings.data_dir / STATUS_FILE).unlink()
        journal.close()
