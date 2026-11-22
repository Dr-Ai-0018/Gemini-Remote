"""Relay-side SQLite journal: incoming commands and the outgoing event outbox.

Commands (from the Gateway) are written here before the Relay acks them, so an
ack always means "on disk". The Gateway may offer the same command again after
a reconnect; recording it is idempotent by job_id.

  pending --handed to the browser--> delivered --succeeded/failed--> finished
  pending --deadline passed before delivery--> expired

A command is marked delivered *before* it is sent to the browser and is never
handed over again, so a crash in between loses the job (the Gateway times it
out) instead of sending it twice.

Events (from the browser) go into the outbox first and leave it only when the
Gateway acks them. Re-sending is safe: both sides de-duplicate by event_id.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any
import json
import sqlite3
import time

from gemini_remote.contracts.envelope import MAX_MESSAGE_BYTES, MAX_RESULT_BYTES, canonical
from gemini_remote.contracts.models import SendMode

MAX_ID_CHARS = 128
EVENT_KINDS = frozenset({"running", "unconfirmed", "succeeded", "failed"})
FINAL_KINDS = frozenset({"succeeded", "failed"})
# Room for the envelope around an event; anything bigger could never be sent.
MAX_EVENT_BYTES = min(MAX_RESULT_BYTES + 16 * 1024, MAX_MESSAGE_BYTES - 4 * 1024)

SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
  job_id TEXT PRIMARY KEY,
  task TEXT NOT NULL,
  mode TEXT NOT NULL,
  deadline_at REAL NOT NULL,
  state TEXT NOT NULL,
  received_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
  event_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES commands(job_id),
  body TEXT NOT NULL,
  created_at REAL NOT NULL,
  acked_at REAL
);
CREATE INDEX IF NOT EXISTS outbox_unacked ON outbox(acked_at, created_at);
"""


def _id(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_ID_CHARS


class RelayJournal:
    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time):
        self.clock = clock
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- commands from the Gateway ------------------------------------------

    def record_command(self, command: dict[str, Any]) -> bool:
        """Persist a command. True if new, False if this job_id was already recorded."""
        job_id, task, mode, deadline = (command.get(k) for k in ("job_id", "task", "mode", "deadline_at"))
        if (not _id(job_id) or not isinstance(task, str) or not task or mode not in SendMode
                or not isinstance(deadline, int | float) or isinstance(deadline, bool)):
            raise ValueError("invalid_command")
        now = self.clock()
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO commands (job_id, task, mode, deadline_at, state, received_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?)",
                (job_id, task, str(mode), float(deadline), "pending", now, now))
        return cur.rowcount == 1

    def command_state(self, job_id: str) -> str | None:
        row = self.db.execute("SELECT state FROM commands WHERE job_id=?", (job_id,)).fetchone()
        return row["state"] if row else None

    def next_pending(self) -> dict[str, Any] | None:
        """Oldest command not yet handed to the browser. Expired ones are retired, never handed over."""
        now = self.clock()
        with self.db:
            self.db.execute("UPDATE commands SET state='expired', updated_at=? WHERE state='pending'"
                            " AND deadline_at<=?", (now, now))
        row = self.db.execute("SELECT job_id, task, mode, deadline_at FROM commands WHERE state='pending'"
                              " ORDER BY received_at, rowid LIMIT 1").fetchone()
        return dict(row) if row else None

    def mark_delivered(self, job_id: str) -> bool:
        """Claim a pending command for the browser. False if it was already claimed."""
        with self.db:
            cur = self.db.execute("UPDATE commands SET state='delivered', updated_at=? WHERE job_id=?"
                                  " AND state='pending'", (self.clock(), job_id))
        return cur.rowcount == 1

    # --- events from the browser --------------------------------------------

    def add_event(self, event: dict[str, Any]) -> bool:
        """Queue a browser event for the Gateway. True if new, False if already queued."""
        if (not isinstance(event, dict) or not _id(event.get("event_id")) or not _id(event.get("job_id"))
                or event.get("kind") not in EVENT_KINDS):
            raise ValueError("invalid_event")
        body = canonical(event)
        if len(body) > MAX_EVENT_BYTES:
            raise ValueError("event_too_large")
        now = self.clock()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            state = self.command_state(event["job_id"])
            if state not in ("delivered", "finished"):
                raise ValueError("unknown_job")
            cur = self.db.execute(
                "INSERT OR IGNORE INTO outbox (event_id, job_id, body, created_at) VALUES (?,?,?,?)",
                (event["event_id"], event["job_id"], body.decode("utf-8"), now))
            if cur.rowcount and event["kind"] in FINAL_KINDS:
                self.db.execute("UPDATE commands SET state='finished', updated_at=? WHERE job_id=?",
                                (now, event["job_id"]))
        return cur.rowcount == 1

    def unacked_events(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT body FROM outbox WHERE acked_at IS NULL ORDER BY created_at, rowid")
        return [json.loads(r["body"]) for r in rows]

    def mark_event_acked(self, event_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE outbox SET acked_at=? WHERE event_id=? AND acked_at IS NULL",
                            (self.clock(), event_id))

    def counts(self) -> dict[str, int]:
        pending = self.db.execute("SELECT COUNT(*) FROM commands WHERE state='pending'").fetchone()[0]
        outbox = self.db.execute("SELECT COUNT(*) FROM outbox WHERE acked_at IS NULL").fetchone()[0]
        return {"pending_commands": pending, "outbox": outbox}
