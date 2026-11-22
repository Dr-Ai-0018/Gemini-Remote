"""Bus configuration from GEMINI_REMOTE_* variables and an optional local .env.

Only this application's own credentials live here. Google login state stays in
the browser and is never read, copied or configured.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import os

PREFIX = "GEMINI_REMOTE_"
DEFAULT_GATEWAY_PORT = 18797
DEFAULT_RELAY_PORT = 18776
MIN_SECRET_LENGTH = 32

SECRET_KEYS = ("control_token", "device_token", "signing_key", "browser_token")
# Each side keeps only what it needs: the VPS never holds the browser pairing
# token, the owner PC never holds the MCP control token.
REQUIRED_SECRETS = {
    "gateway": frozenset({"control_token", "device_token", "signing_key"}),
    "relay": frozenset({"device_token", "signing_key", "browser_token"}),
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


@dataclass(frozen=True)
class Settings:
    control_token: str = ""
    device_token: str = ""
    signing_key: str = ""
    browser_token: str = ""
    device_id: str = "owner-pc"
    gateway_url: str = f"ws://127.0.0.1:{DEFAULT_GATEWAY_PORT}/ws/device"
    data_dir: Path = Path(".data")

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
        return cls(
            **secrets,
            device_id=merged.get(PREFIX + "DEVICE_ID", defaults.device_id),
            gateway_url=merged.get(PREFIX + "GATEWAY_URL", defaults.gateway_url),
            data_dir=Path(merged.get(PREFIX + "DATA_DIR", str(defaults.data_dir))),
        )
