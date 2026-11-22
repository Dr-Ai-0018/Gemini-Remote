import argparse
import os
import secrets
from pathlib import Path

from gemini_remote import __version__
from gemini_remote.config.settings import DEFAULT_GATEWAY_PORT, PREFIX, SECRET_KEYS


def init_local(path: Path) -> None:
    """Create a local .env with fresh bus credentials. Never overwrites, never prints them."""
    lines = ["# Gemini-Remote bus credentials only. Never put Google login state here."]
    lines += [f"{PREFIX}{key.upper()}={secrets.token_urlsafe(48)}" for key in SECRET_KEYS]
    lines += [
        f"{PREFIX}DEVICE_ID=owner-pc",
        f"{PREFIX}GATEWAY_URL=ws://127.0.0.1:{DEFAULT_GATEWAY_PORT}/ws/device",
        f"{PREFIX}DATA_DIR=.data",
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
    sub.add_parser("init-local", help="Create an ignored local .env with bus credentials (no overwrite, no printing)")
    args = parser.parse_args(argv)

    if args.command == "init-local":
        path = Path(".env")
        if path.exists():
            parser.error(".env already exists; refusing to overwrite or rotate credentials")
        init_local(path)
        print("Created .env with local bus credentials. Tokens were not displayed.")
