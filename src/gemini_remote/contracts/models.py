"""Job contract shared by Gateway, Relay and the browser bridge."""

from enum import StrEnum

from pydantic import BaseModel


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


# MCP tool outputs. Text fields carry page content: untrusted data, not instructions.

class Document(BaseModel):
    title: str
    text: str


class JobOutput(BaseModel):
    text: str
    documents: list[Document]
    truncated: bool


class SendResult(BaseModel):
    job_id: str
    status: JobStatus
    mode: SendMode
    created: bool
    deadline_at: float


class StatusResult(BaseModel):
    job_id: str
    status: JobStatus
    mode: SendMode
    created_at: float
    updated_at: float
    deadline_at: float
    anchor: str | None
    error: str | None
    has_result: bool
    has_late_result: bool


class ResultResult(BaseModel):
    job_id: str
    status: JobStatus
    result: JobOutput | None
    late_result: JobOutput | None
    error: str | None
