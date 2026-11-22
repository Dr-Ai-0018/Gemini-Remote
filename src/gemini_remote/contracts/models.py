"""Job contract shared by Gateway, Relay and the browser bridge."""

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # The send click happened (or may have) but no page anchor proved it.
    # Never re-sent; a read-only reconcile may still find the anchor later.
    UNCONFIRMED = "unconfirmed"
    # The Gateway budget expired; the page may keep working. A late result is
    # stored with a flag instead of rewriting this status.
    TIMED_OUT = "timed_out"


TERMINAL_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMED_OUT})


class SendMode(StrEnum):
    TASK = "task"
    CHAT = "chat"


DEFAULT_SEND_MODE = SendMode.TASK
