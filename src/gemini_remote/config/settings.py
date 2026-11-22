"""Configuration from GEMINI_REMOTE_* variables and an optional local .env.

Everything personal to one installation (credentials, ports, preferences)
lives in the ignored local .env, never in tracked code. The browser extension
is not configured here: it pairs with the Relay at runtime (storage.pairing).
Only this application's own settings live here. Google login state stays in
the browser and is never read, copied or configured.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import os

from gemini_remote.contracts.models import DEFAULT_SEND_MODE, SendMode

PREFIX = "GEMINI_REMOTE_"
DEFAULT_GATEWAY_PORT = 18797
DEFAULT_RELAY_PORT = 18776
MIN_SECRET_LENGTH = 32

SECRET_KEYS = ("control_token", "device_token", "signing_key")
# Each side keeps only what it needs: the local PC never holds the MCP control token.
REQUIRED_SECRETS = {
    "gateway": frozenset({"control_token", "device_token", "signing_key"}),
    "relay": frozenset({"device_token", "signing_key"}),
    "client": frozenset({"control_token"}),
}


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=value lines with our prefix; everything else is ignored."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith(PREFIX):
            values[key] = value.strip()
    return values


def _port(merged: Mapping[str, str], name: str, default: int) -> int:
    raw = merged.get(PREFIX + name, str(default))
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise ValueError(f"Invalid {PREFIX}{name}: {raw!r}")
    return int(raw)


@dataclass(frozen=True)
class Settings:
    control_token: str = ""
    device_token: str = ""
    signing_key: str = ""
    device_id: str = "local-pc"
    gateway_url: str = f"ws://127.0.0.1:{DEFAULT_GATEWAY_PORT}/ws/device"
    gateway_port: int = DEFAULT_GATEWAY_PORT
    relay_port: int = DEFAULT_RELAY_PORT
    data_dir: Path = Path(".data")
    default_mode: SendMode = DEFAULT_SEND_MODE

    @classmethod
    def from_env(
        cls,
        role: str,
        environ: Mapping[str, str] | None = None,
        env_file: Path = Path(".env"),
    ) -> "Settings":
        """Load settings for one role; process environment wins over the file."""
        if role not in REQUIRED_SECRETS:
            raise ValueError(f"Unknown role {role!r}")
        merged = read_env_file(env_file)
        merged.update({k: v for k, v in (os.environ if environ is None else environ).items()
                       if k.startswith(PREFIX)})
        secrets: dict[str, str] = {}
        for key in SECRET_KEYS:
            name = PREFIX + key.upper()
            value = merged.get(name, "")
            if key in REQUIRED_SECRETS[role] and (
                len(value) < MIN_SECRET_LENGTH or value.startswith("replace-")
            ):
                raise ValueError(f"Missing or invalid {name}; run `gemini-remote init-local`")
            secrets[key] = value
        defaults = cls()
        mode = merged.get(PREFIX + "DEFAULT_MODE", defaults.default_mode)
        if mode not in SendMode:
            raise ValueError(f"Invalid {PREFIX}DEFAULT_MODE: {mode!r}")
        return cls(
            **secrets,
            device_id=merged.get(PREFIX + "DEVICE_ID", defaults.device_id),
            gateway_url=merged.get(PREFIX + "GATEWAY_URL", defaults.gateway_url),
            gateway_port=_port(merged, "GATEWAY_PORT", DEFAULT_GATEWAY_PORT),
            relay_port=_port(merged, "RELAY_PORT", DEFAULT_RELAY_PORT),
            data_dir=Path(merged.get(PREFIX + "DATA_DIR", str(defaults.data_dir))),
            default_mode=SendMode(mode),
        )
