import argparse
import base64
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from gemini_remote import __version__
from gemini_remote.config.settings import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_RELAY_PORT,
    PREFIX,
    SECRET_KEYS,
    Settings,
)


def new_extension_public_key() -> str:
    """Fresh RSA key for a stable extension ID; only the public half is kept."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(der).decode("ascii")


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
        f"{PREFIX}EXTENSION_KEY={new_extension_public_key()}",
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
                    log_level="info", access_log=False)
