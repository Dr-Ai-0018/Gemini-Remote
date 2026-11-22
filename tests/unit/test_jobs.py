import pytest

from gemini_remote.contracts.envelope import MAX_RESULT_BYTES
from gemini_remote.storage.jobs import IdempotencyConflict, JobNotFound, JobStore

RESULT = {"text": "summary", "documents": [{"title": "Doc", "text": "body"}], "truncated": False}


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = JobStore(tmp_path / "jobs.sqlite3", clock=clock)
    yield s
    s.close()


def send(store, key="k1", task="write a plan", mode="task", timeout=600):
    return store.submit(task, key, mode, timeout)


def event(store, job_id, kind, event_id=None, **extra):
    return store.apply_event({"event_id": event_id or f"{job_id}-{kind}", "job_id": job_id, "kind": kind, **extra})


def dispatch(store):
    command = store.next_command()
    assert store.ack_command(command["job_id"])
    return command["job_id"]


def test_submit_returns_queued_job_immediately(store):
    job = send(store)
    assert job["created"] is True
    assert job["status"] == "queued"
    assert job["mode"] == "task"


def test_same_key_same_request_returns_the_original(store):
    first = send(store)
    again = send(store)
    assert again["job_id"] == first["job_id"]
    assert again["created"] is False


@pytest.mark.parametrize("change", [{"task": "other"}, {"mode": "chat"}, {"timeout": 900}])
def test_same_key_different_request_is_refused(store, change):
    send(store)
    with pytest.raises(IdempotencyConflict):
        send(store, **change)


@pytest.mark.parametrize(
    "args",
    [{"task": "  "}, {"task": "x" * 32_001}, {"key": ""}, {"mode": "deep"}, {"timeout": 10}, {"timeout": True}],
)
def test_invalid_submissions_are_rejected(store, args):
    with pytest.raises(ValueError):
        send(store, **args)


def test_only_one_job_is_active_at_a_time(store):
    first = send(store, "a")["job_id"]
    second = send(store, "b", task="second")["job_id"]
    command = store.next_command()
    assert command["job_id"] == first
    assert command["task"] == "write a plan"
    # Offered but not yet persisted by the Relay: offering it again is safe.
    assert store.next_command()["job_id"] == first
    assert store.ack_command(first)
    assert store.next_command() is None
    event(store, first, "running", anchor="turn-1")
    assert store.next_command() is None
    event(store, first, "succeeded", result=RESULT)
    assert store.next_command()["job_id"] == second


def test_ack_is_idempotent_and_unknown_job_is_reported(store):
    job = send(store)["job_id"]
    assert store.ack_command(job)
    assert store.ack_command(job)
    with pytest.raises(JobNotFound):
        store.ack_command("missing")


def test_happy_path_stores_anchor_and_result(store):
    job = send(store)["job_id"]
    dispatch(store)
    assert event(store, job, "running", anchor="turn-1")["status"] == "running"
    assert event(store, job, "succeeded", result=RESULT)["status"] == "succeeded"
    status = store.status(job)
    assert status["anchor"] == "turn-1"
    assert status["has_result"] is True
    assert store.result(job)["result"] == RESULT


def test_unconfirmed_is_never_offered_again_but_can_be_reconciled(store):
    job = send(store)["job_id"]
    dispatch(store)
    event(store, job, "unconfirmed")
    assert store.next_command() is None
    assert event(store, job, "succeeded", event_id="reconciled", result=RESULT)["status"] == "succeeded"


def test_unconfirmed_does_not_block_the_queue(store):
    first = send(store, "a")["job_id"]
    dispatch(store)
    event(store, first, "unconfirmed")
    second = send(store, "b", task="second")["job_id"]
    assert store.next_command()["job_id"] == second


def test_duplicate_events_are_applied_once(store):
    job = send(store)["job_id"]
    dispatch(store)
    assert event(store, job, "running", event_id="e1", anchor="turn-1")["duplicate"] is False
    again = event(store, job, "running", event_id="e1", anchor="turn-1")
    assert again["duplicate"] is True and again["status"] == "running"


@pytest.mark.parametrize(
    ("setup", "kind", "extra"),
    [
        ([], "running", {"anchor": "t"}),                       # queued -> running skips dispatch
        (["dispatch", "running"], "unconfirmed", {}),          # anchor already proven
        (["dispatch", "succeeded"], "failed", {"error": "x"}),  # terminal stays terminal
    ],
)
def test_illegal_transitions_are_recorded_but_not_applied(store, setup, kind, extra):
    job = send(store)["job_id"]
    for step in setup:
        if step == "dispatch":
            dispatch(store)
        elif step == "running":
            event(store, job, "running", anchor="turn-1")
        elif step == "succeeded":
            event(store, job, "succeeded", result=RESULT)
    before = store.status(job)["status"]
    outcome = event(store, job, kind, event_id="late", **extra)
    assert outcome["applied"] is False
    assert store.status(job)["status"] == before


@pytest.mark.parametrize(
    ("kind", "extra"),
    [("running", {}), ("failed", {}), ("succeeded", {"result": {"text": "x"}}), ("cancelled", {})],
)
def test_events_missing_their_evidence_are_rejected(store, kind, extra):
    job = send(store)["job_id"]
    dispatch(store)
    with pytest.raises(ValueError):
        event(store, job, kind, **extra)


def test_oversized_result_is_rejected(store):
    job = send(store)["job_id"]
    dispatch(store)
    big = {**RESULT, "text": "x" * MAX_RESULT_BYTES}
    with pytest.raises(ValueError, match="result_too_large"):
        event(store, job, "succeeded", result=big)


def test_deadline_times_out_and_late_result_is_kept_separately(store, clock):
    job = send(store, timeout=60)["job_id"]
    dispatch(store)
    event(store, job, "running", anchor="turn-1")
    clock.now += 61
    assert store.expire() == [job]
    outcome = event(store, job, "succeeded", result=RESULT)
    assert outcome["status"] == "timed_out"
    result = store.result(job)
    assert result["result"] is None
    assert result["late_result"] == RESULT
    assert result["error"] == "deadline_exceeded"


def test_queued_job_past_deadline_is_never_dispatched(store, clock):
    job = send(store, timeout=60)["job_id"]
    clock.now += 61
    assert store.next_command() is None
    assert store.status(job)["status"] == "timed_out"


def test_state_survives_reopen(tmp_path, clock):
    path = tmp_path / "jobs.sqlite3"
    first = JobStore(path, clock=clock)
    job = send(first)["job_id"]
    dispatch(first)
    first.close()
    second = JobStore(path, clock=clock)
    try:
        assert second.status(job)["status"] == "dispatched"
        assert send(second)["job_id"] == job
    finally:
        second.close()
