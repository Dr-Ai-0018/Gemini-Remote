import argparse
import asyncio
import logging
import os
import secrets
import sys
import time
from contextlib import suppress
from pathlib import Path

from gemini_remote import __version__
from gemini_remote.config.settings import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_RELAY_PORT,
    PREFIX,
    SECRET_KEYS,
    Settings,
)


def init_local(path: Path) -> None:
    """Create a local .env with fresh credentials and settings. Never overwrites, never prints tokens."""
    defaults = Settings()
    lines = ["# Gemini-Remote local settings. Never put Google login state here."]
    lines += [f"{PREFIX}{key.upper()}={secrets.token_urlsafe(48)}" for key in SECRET_KEYS]
    lines += [
        f"{PREFIX}DEVICE_ID={defaults.device_id}",
        f"{PREFIX}GATEWAY_URL=ws://127.0.0.1:{DEFAULT_GATEWAY_PORT}/ws/device",
        f"{PREFIX}GATEWAY_PORT={DEFAULT_GATEWAY_PORT}",
        f"{PREFIX}RELAY_PORT={DEFAULT_RELAY_PORT}",
        f"{PREFIX}DATA_DIR={defaults.data_dir.as_posix()}",
        f"{PREFIX}DEFAULT_MODE={defaults.default_mode}",
    ]
    with path.open("x", encoding="utf-8", newline="\n") as out:
        out.write("\n".join(lines) + "\n")
    if os.name != "nt":
        path.chmod(0o600)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="gemini-remote",
        description="Asynchronous Gemini web tasks over MCP; Google login state stays in the browser",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-local", help="Create an ignored local .env with credentials and settings (no overwrite, no printing)")
    gateway = sub.add_parser("gateway", help="Run the Gateway (MCP at /mcp/, Relay socket at /ws/device)")
    gateway.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback only)")
    gateway.add_argument("--port", type=int, help="bind port (default: GEMINI_REMOTE_GATEWAY_PORT)")
    sub.add_parser("relay", help="Run the Relay on this PC (extension socket on 127.0.0.1, dials the Gateway)")
    pair = sub.add_parser("pair", help="Show a new one-time code for pairing the browser extension with this Relay")
    pair.add_argument("--reset", action="store_true", help="forget the paired extension instead")
    sub.add_parser("doctor", help="Read-only check of local settings and the running Relay")
    args = parser.parse_args(argv)

    if args.command == "init-local":
        path = Path(".env")
        if path.exists():
            parser.error(".env already exists; refusing to overwrite or rotate credentials")
        init_local(path)
        print("Created .env with local credentials and settings. Tokens were not displayed.")
    elif args.command == "gateway":
        import uvicorn

        from gemini_remote.gateway.app import create_app

        try:
            settings = Settings.from_env("gateway")
        except ValueError as exc:
            parser.error(str(exc))
        uvicorn.run(create_app(settings), host=args.host, port=args.port or settings.gateway_port,
                    log_level="info", access_log=False, ws="websockets-sansio")
    elif args.command == "relay":
        from gemini_remote.relay.service import run_relay

        try:
            settings = Settings.from_env("relay")
        except ValueError as exc:
            parser.error(str(exc))
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        with suppress(KeyboardInterrupt):
            asyncio.run(run_relay(settings))
    elif args.command == "pair":
        from gemini_remote.relay.service import JOURNAL_FILE, announce_code
        from gemini_remote.relay.state import read_status
        from gemini_remote.storage.pairing import PairingStore

        try:
            settings = Settings.from_env("relay")
        except ValueError as exc:
            parser.error(str(exc))
        store = PairingStore(settings.data_dir / JOURNAL_FILE)
        try:
            if args.reset:
                store.reset()
                print("Unpaired. The extension is disconnected within about 30 seconds;"
                      " run `gemini-remote pair` to pair again.")
            else:
                status = read_status(settings.data_dir)
                running = status is not None and time.time() - float(status.get("updated_at", 0)) <= 20
                if not running and not store.status()["paired"]:
                    # The Relay makes and shows a fresh code itself when it starts unpaired.
                    print("The Relay is not running. Start it with `gemini-remote relay`;"
                          " it shows a pairing code when it starts.")
                else:
                    announce_code(store.new_code(), settings.relay_port)
                    if not running:
                        print("The Relay is not running; start it with `gemini-remote relay` before entering the code.")
        finally:
            store.close()
    elif args.command == "doctor":
        from gemini_remote.cli.doctor import doctor

        sys.exit(doctor())
