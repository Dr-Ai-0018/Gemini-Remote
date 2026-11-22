"""SQLite job store: idempotent submit, legal transitions only, never re-send.

Lifecycle (one command per job, command id == job id):
  queued --Relay persisted the command--> dispatched
  dispatched --page anchor found--> running --> succeeded | failed
  dispatched --click happened, no anchor--> unconfirmed --reconcile--> running | succeeded | failed
  dispatched --refused before any click--> failed
  any non-terminal --deadline--> timed_out (a later result is kept as late_result)

An unconfirmed job is never offered to the Relay again. Only one job may be
dispatched or running at a time.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any
import hashlib
import json
import sqlite3
import time
import uuid

from gemini_remote.contracts.envelope import MAX_RESULT_BYTES, canonical
from gemini_remote.contracts.models import TERMINAL_STATUSES, JobStatus, SendMode

MAX_TASK_CHARS = 32_000
MAX_KEY_CHARS = 128
MIN_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 4 * 3600
DEFAULT_TIMEOUT_SECONDS = 1800

S = JobStatus
ACTIVE = (S.DISPATCHED, S.RUNNING)
# Event kind -> statuses it may move a job out of.
TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    S.RUNNING: frozenset({S.DISPATCHED, S.UNCONFIRMED}),
    S.UNCONFIRMED: frozenset({S.DISPATCHED}),
    S.SUCCEEDED: frozenset({S.DISPATCHED, S.RUNNING, S.UNCONFIRMED}),
    S.FAILED: frozenset({S.DISPATCHED, S.RUNNING, S.UNCONFIRMED}),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  request_hash TEXT NOT NULL,
  task TEXT NOT NULL,
  mode TEXT NOT NULL,
  timeout_seconds INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  deadline_at REAL NOT NULL,
  anchor TEXT,
  error TEXT,
  result TEXT,
  late_result TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  kind TEXT NOT NULL,
  received_at REAL NOT NULL,
  applied INTEGER NOT NULL
);
"""


class JobNotFound(ValueError):
    def __init__(self) -> None:
        super().__init__("job_not_found")


class IdempotencyConflict(ValueError):
    def __init__(self) -> None:
        super().__init__("idempotency_key_reused_with_different_request")


def _check_result(result: Any) -> dict[str, Any]:
    """Result = {"text": str, "documents": [{"title": str, "text": str}], "truncated": bool}."""
    if (not isinstance(result, dict) or set(result) != {"text", "documents", "truncated"}
            or not isinstance(result["text"], str) or not isinstance(result["truncated"], bool)
            or not isinstance(result["documents"], list)
            or any(not isinstance(d, dict) or set(d) != {"title", "text"}
                   or not all(isinstance(v, str) for v in d.values()) for d in result["documents"])):
        raise ValueError("invalid_result")
    if len(canonical(result)) > MAX_RESULT_BYTES:
        raise ValueError("result_too_large")
    return result


class JobStore:
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

    # --- MCP side -----------------------------------------------------------

    def submit(self, task: str, idempotency_key: str, mode: SendMode | str,
               timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
        if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK_CHARS:
            raise ValueError(f"task must be 1..{MAX_TASK_CHARS} characters")
        if not isinstance(idempotency_key, str) or not 0 < len(idempotency_key) <= MAX_KEY_CHARS:
            raise ValueError(f"idempotency_key must be 1..{MAX_KEY_CHARS} characters")
        if mode not in SendMode:
            raise ValueError("mode must be task or chat")
        if (not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool)
                or not MIN_TIMEOUT_SECONDS <= timeout_seconds <= MAX_TIMEOUT_SECONDS):
            raise ValueError(f"timeout_seconds must be {MIN_TIMEOUT_SECONDS}..{MAX_TIMEOUT_SECONDS}")
        mode = SendMode(mode)
        request_hash = hashlib.sha256(
            canonical({"task": task, "mode": mode.value, "timeout_seconds": timeout_seconds})
        ).hexdigest()
        now = self.clock()
        job_id = uuid.uuid4().hex
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT id, request_hash FROM jobs WHERE idempotency_key=?",
                                  (idempotency_key,)).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise IdempotencyConflict()
                return {**self.status(row["id"]), "created": False}
            self.db.execute(
                "INSERT INTO jobs (id, idempotency_key, request_hash, task, mode, timeout_seconds,"
                " status, created_at, updated_at, deadline_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_id, idempotency_key, request_hash, task, mode.value, timeout_seconds,
                 S.QUEUED.value, now, now, now + timeout_seconds),
            )
        return {**self.status(job_id), "created": True}

    def _row(self, job_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound()
        return row

    def status(self, job_id: str) -> dict[str, Any]:
        row = self._row(job_id)
        return {
            "job_id": row["id"],
            "status": row["status"],
            "mode": row["mode"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "deadline_at": row["deadline_at"],
            "anchor": row["anchor"],
            "error": row["error"],
            "has_result": row["result"] is not None,
            "has_late_result": row["late_result"] is not None,
        }

    def result(self, job_id: str) -> dict[str, Any]:
        row = self._row(job_id)
        return {
            "job_id": row["id"],
            "status": row["status"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "late_result": json.loads(row["late_result"]) if row["late_result"] else None,
            "error": row["error"],
        }

    # --- Relay side ---------------------------------------------------------

    def next_command(self) -> dict[str, Any] | None:
        """Oldest queued job, but only while nothing is dispatched or running."""
        self.expire()
        busy = self.db.execute(
            f"SELECT 1 FROM jobs WHERE status IN ({','.join('?' * len(ACTIVE))}) LIMIT 1",
            [s.value for s in ACTIVE]).fetchone()
        if busy:
            return None
        row = self.db.execute("SELECT * FROM jobs WHERE status=? ORDER BY created_at, rowid LIMIT 1",
                              (S.QUEUED.value,)).fetchone()
        if row is None:
            return None
        return {"job_id": row["id"], "task": row["task"], "mode": row["mode"],
                "deadline_at": row["deadline_at"]}

    def ack_command(self, job_id: str) -> bool:
        """The Relay persisted the command. Idempotent; false if the job moved on."""
        now = self.clock()
        with self.db:
            cur = self.db.execute("UPDATE jobs SET status=?, updated_at=? WHERE id=? AND status=?",
                                  (S.DISPATCHED.value, now, job_id, S.QUEUED.value))
        if cur.rowcount:
            return True
        return self._row(job_id)["status"] != S.QUEUED

    def apply_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Persist one Relay event exactly once. Returns {"applied", "status"}.

        event = {"event_id", "job_id", "kind": running|unconfirmed|succeeded|failed,
                 "anchor"?: str, "result"?: Result, "error"?: str}
        """
        event_id, job_id, kind = event.get("event_id"), event.get("job_id"), event.get("kind")
        if (not isinstance(event_id, str) or not 0 < len(event_id) <= MAX_KEY_CHARS
                or not isinstance(job_id, str) or kind not in {k.value for k in TRANSITIONS}):
            raise ValueError("invalid_event")
        kind = JobStatus(kind)
        anchor, error = event.get("anchor"), event.get("error")
        if anchor is not None and (not isinstance(anchor, str) or len(anchor) > MAX_KEY_CHARS):
            raise ValueError("invalid_event")
        if error is not None and (not isinstance(error, str) or len(error) > 500):
            raise ValueError("invalid_event")
        if kind is S.RUNNING and not anchor:
            raise ValueError("running_requires_anchor")
        result = _check_result(event["result"]) if kind is S.SUCCEEDED else None
        if kind is S.FAILED and not error:
            raise ValueError("failed_requires_error")

        now = self.clock()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self._row(job_id)
            seen = self.db.execute("SELECT applied FROM events WHERE id=?", (event_id,)).fetchone()
            if seen is not None:
                return {"applied": bool(seen["applied"]), "status": row["status"], "duplicate": True}
            current = JobStatus(row["status"])
            applied = True
            if current is S.TIMED_OUT and kind is S.SUCCEEDED:
                self.db.execute("UPDATE jobs SET late_result=?, updated_at=? WHERE id=?",
                                (json.dumps(result, ensure_ascii=False), now, job_id))
            elif current in TRANSITIONS[kind]:
                self.db.execute(
                    "UPDATE jobs SET status=?, updated_at=?, anchor=COALESCE(?, anchor),"
                    " error=COALESCE(?, error), result=COALESCE(?, result) WHERE id=?",
                    (kind.value, now, anchor, error,
                     json.dumps(result, ensure_ascii=False) if result else None, job_id))
                current = kind
            else:
                applied = False
            self.db.execute("INSERT INTO events (id, job_id, kind, received_at, applied) VALUES (?,?,?,?,?)",
                            (event_id, job_id, kind.value, now, int(applied)))
        return {"applied": applied, "status": current.value, "duplicate": False}

    def expire(self) -> list[str]:
        now = self.clock()
        terminal = [s.value for s in TERMINAL_STATUSES]
        with self.db:
            rows = self.db.execute(
                f"UPDATE jobs SET status=?, updated_at=?, error=COALESCE(error, 'deadline_exceeded')"
                f" WHERE deadline_at<=? AND status NOT IN ({','.join('?' * len(terminal))}) RETURNING id",
                [S.TIMED_OUT.value, now, now, *terminal]).fetchall()
        return [r["id"] for r in rows]
