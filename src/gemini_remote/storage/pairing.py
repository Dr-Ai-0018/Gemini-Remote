"""Pairing between the Relay and one browser extension.

The extension build is generic (no per-installation key), so trust is set up
after install: the Relay shows a short one-time code in its terminal, the user
types it into the panel on the Gemini page, and the Relay hands back a
long-lived browser token. Only hashes are stored, together with the Origin of
the extension that paired; later connections must present the token from that
same Origin.

One pairing at a time: redeeming a new code replaces the previous pairing.
The table lives in the Relay's SQLite file so `gemini-remote pair` (a separate
process) and the running Relay see the same state.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any
import hashlib
import hmac
import re
import secrets
import sqlite3
import time

CODE_DIGITS = 6
CODE_TTL_SECONDS = 300
MAX_CODE_FAILURES = 5
EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pairing (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  token_hash TEXT,
  origin TEXT,
  paired_at REAL,
  code_hash TEXT,
  code_expires_at REAL,
  code_failures INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO pairing (id) VALUES (1);
"""


class PairingError(Exception):
    """Redeeming a code failed: `no_code` (none active, expired or used up) or `bad_code`."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_extension_origin(origin: str | None) -> bool:
    return isinstance(origin, str) and EXTENSION_ORIGIN.fullmatch(origin) is not None


def read_pairing(path: Path) -> dict[str, Any] | None:
    """Read-only view for doctor; None when the Relay has never created its database."""
    if not path.exists():
        return None
    try:
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = db.execute("SELECT origin, paired_at FROM pairing WHERE id=1 AND token_hash IS NOT NULL").fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return {"paired": row is not None, "origin": row[0] if row else None, "paired_at": row[1] if row else None}


class PairingStore:
    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time):
        self.clock = clock
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def _row(self) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM pairing WHERE id=1").fetchone()

    def new_code(self) -> str:
        """A fresh one-time code; any earlier code stops working. The current pairing is kept until redeemed."""
        code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
        with self.db:
            self.db.execute("UPDATE pairing SET code_hash=?, code_expires_at=?, code_failures=0 WHERE id=1",
                            (_hash(code), self.clock() + CODE_TTL_SECONDS))
        return code

    def redeem(self, code: Any, origin: str) -> str:
        """Trade a valid code for a new browser token bound to `origin`."""
        if not is_extension_origin(origin):
            raise PairingError("bad_origin")
        token = None
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self._row()
            if (row["code_hash"] is None or row["code_expires_at"] <= self.clock()
                    or row["code_failures"] >= MAX_CODE_FAILURES):
                error = "no_code"
            elif not isinstance(code, str) or not hmac.compare_digest(_hash(code.strip()), row["code_hash"]):
                error = "bad_code"
                failures = row["code_failures"] + 1
                if failures >= MAX_CODE_FAILURES:  # too many guesses: the code is burned
                    self.db.execute("UPDATE pairing SET code_hash=NULL, code_expires_at=NULL,"
                                    " code_failures=? WHERE id=1", (failures,))
                else:
                    self.db.execute("UPDATE pairing SET code_failures=? WHERE id=1", (failures,))
            else:
                error = None
                token = secrets.token_urlsafe(32)
                self.db.execute("UPDATE pairing SET token_hash=?, origin=?, paired_at=?, code_hash=NULL,"
                                " code_expires_at=NULL, code_failures=0 WHERE id=1",
                                (_hash(token), origin, self.clock()))
        if token is None:
            raise PairingError(error)
        return token

    def check(self, token: Any, origin: str | None) -> bool:
        row = self._row()
        if row["token_hash"] is None or not isinstance(token, str) or origin != row["origin"]:
            return False
        return hmac.compare_digest(_hash(token), row["token_hash"])

    def reset(self) -> None:
        """Forget the paired extension and any outstanding code."""
        with self.db:
            self.db.execute("UPDATE pairing SET token_hash=NULL, origin=NULL, paired_at=NULL, code_hash=NULL,"
                            " code_expires_at=NULL, code_failures=0 WHERE id=1")

    def status(self) -> dict[str, Any]:
        row = self._row()
        code_active = (row["code_hash"] is not None and row["code_expires_at"] > self.clock()
                       and row["code_failures"] < MAX_CODE_FAILURES)
        return {"paired": row["token_hash"] is not None, "origin": row["origin"],
                "paired_at": row["paired_at"], "code_active": code_active}
