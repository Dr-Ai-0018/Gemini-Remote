"""`gemini-remote doctor`: read-only health check for the local PC.

Reads the local settings and the status file the running Relay keeps in the
data directory. It opens no network connections and never prints credentials.
"""

from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urlsplit
import time

from gemini_remote import __version__
from gemini_remote.config.settings import Settings
from gemini_remote.relay.state import read_status

STALE_AFTER_SECONDS = 20.0


def _where(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else "(invalid)"


def doctor(env_file: Path = Path(".env"), environ: Mapping[str, str] | None = None,
           now: float | None = None, out: Callable[[str], None] = print) -> int:
    """Print a report; return 0 when the Relay side is healthy, 1 otherwise."""
    problems = 0
    out(f"gemini-remote {__version__} doctor")
    out("config (.env):")
    relay: Settings | None = None
    for role in ("relay", "gateway", "client"):
        try:
            settings = Settings.from_env(role, environ=environ, env_file=env_file)
        except ValueError as exc:
            if role == "relay":
                out(f"  {role:<8} problem: {exc}")
                problems += 1
            else:  # the Gateway and the MCP client usually live elsewhere
                out(f"  {role:<8} not set up on this machine ({exc})")
            continue
        out(f"  {role:<8} ok")
        if role == "relay":
            relay = settings
    if relay is None:
        out("relay settings are incomplete; fix .env first (on the VPS only the gateway role matters)")
        return 1

    out(f"ports: gateway {relay.gateway_port}, relay {relay.relay_port} (loopback)")
    out(f"gateway: {_where(relay.gateway_url)} as device {relay.device_id!r}")
    out(f"extension id: {relay.extension_origin.removeprefix('chrome-extension://')}")

    status = read_status(relay.data_dir)
    if status is None:
        out("relay: not running (no status file in the data directory)")
        return 1
    age = (time.time() if now is None else now) - float(status.get("updated_at", 0))
    if age > STALE_AFTER_SECONDS:
        out(f"relay: last status is {age:.0f}s old; it is probably not running")
        return 1

    relay_version = status.get("relay_version")
    out(f"relay: running, version {relay_version}")
    if relay_version != __version__:
        out(f"  problem: the running relay is {relay_version} but this checkout is {__version__}; restart it")
        problems += 1

    if status.get("gateway_connected"):
        out(f"gateway: connected, version {status.get('gateway_version')}")
    else:
        out("gateway: not connected (check that the Gateway runs and the URL, token and device id match)")
        problems += 1

    extension_version = status.get("extension_version")
    if status.get("browser_connected"):
        ready = "ready" if status.get("browser_ready") else "not ready (open a Gemini tab)"
        out(f"extension: connected, {ready}, version {extension_version}")
        if extension_version != __version__:
            out(f"  problem: the extension is {extension_version} but this checkout is {__version__};"
                " rebuild it and reload the extension")
            problems += 1
    else:
        out("extension: not connected (is the browser open with the extension loaded?)")
        problems += 1

    out(f"queue: {status.get('pending_commands', 0)} command(s) waiting for the browser,"
        f" {status.get('outbox', 0)} event(s) waiting for the gateway")
    return 0 if problems == 0 else 1
