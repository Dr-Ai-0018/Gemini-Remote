"""Live Relay status, shared by the upstream client, the browser socket and `doctor`."""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import json
import os
import time

STATUS_FILE = "relay-status.json"


@dataclass
class RelayState:
    gateway_connected: bool = False
    gateway_version: str | None = None
    browser_connected: bool = False
    browser_ready: bool = False
    extension_version: str | None = None

    def write(self, data_dir: Path, relay_version: str, counts: dict[str, int]) -> None:
        """Atomically publish the status for `doctor`. Contains no job content or credentials."""
        data_dir.mkdir(parents=True, exist_ok=True)
        body: dict[str, Any] = {"relay_version": relay_version, "updated_at": time.time(),
                                **asdict(self), **counts}
        tmp = data_dir / (STATUS_FILE + ".tmp")
        tmp.write_text(json.dumps(body, indent=2), encoding="utf-8")
        os.replace(tmp, data_dir / STATUS_FILE)


def read_status(data_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((data_dir / STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
