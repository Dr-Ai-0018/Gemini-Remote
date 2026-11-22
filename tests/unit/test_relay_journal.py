import pytest

from gemini_remote.storage.relay_journal import RelayJournal

NOW = 1_000_000.0


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def journal(tmp_path, clock):
    j = RelayJournal(tmp_path / "relay.sqlite3", clock)
    yield j
    j.close()


def command(job_id="j1", deadline=NOW + 600):
    return {"job_id": job_id, "task": "write a plan", "mode": "task", "deadline_at": deadline}


def event(event_id="e1", job_id="j1", kind="running", **extra):
    return {"event_id": event_id, "job_id": job_id, "kind": kind, **extra}


def test_command_is_recorded_once_and_survives_a_restart(tmp_path, clock):
    path = tmp_path / "relay.sqlite3"
    first = RelayJournal(path, clock)
    assert first.record_command(command()) is True
    first.close()
    again = RelayJournal(path, clock)
    assert again.record_command(command()) is False  # re-offered after reconnect
    assert again.next_pending()["job_id"] == "j1"
    again.close()


@pytest.mark.parametrize("bad", [{}, {**command(), "job_id": ""}, {**command(), "mode": "deep"},
                                 {**command(), "task": ""}, {**command(), "deadline_at": "soon"},
                                 {**command(), "deadline_at": True}])
def test_invalid_commands_are_refused(journal, bad):
    with pytest.raises(ValueError, match="invalid_command"):
        journal.record_command(bad)


def test_a_command_is_handed_to_the_browser_at_most_once(journal):
    journal.record_command(command())
    assert journal.mark_delivered("j1") is True
    assert journal.mark_delivered("j1") is False
    assert journal.next_pending() is None
    journal.record_command(command())  # Gateway offers it again: still not re-delivered
    assert journal.next_pending() is None
    assert journal.command_state("j1") == "delivered"


def test_commands_come_out_in_arrival_order(journal, clock):
    journal.record_command(command("j1"))
    clock.now += 1
    journal.record_command(command("j2"))
    assert journal.next_pending()["job_id"] == "j1"
    journal.mark_delivered("j1")
    assert journal.next_pending()["job_id"] == "j2"


def test_expired_command_is_never_handed_over(journal, clock):
    journal.record_command(command(deadline=NOW + 30))
    clock.now += 31
    assert journal.next_pending() is None
    assert journal.command_state("j1") == "expired"
    assert journal.mark_delivered("j1") is False


def test_events_queue_once_until_acked(journal):
    journal.record_command(command())
    journal.mark_delivered("j1")
    assert journal.add_event(event()) is True
    assert journal.add_event(event()) is False  # browser re-sent it
    assert [e["event_id"] for e in journal.unacked_events()] == ["e1"]
    journal.mark_event_acked("e1")
    assert journal.unacked_events() == []
    assert journal.add_event(event()) is False  # still de-duplicated after the ack


def test_final_event_finishes_the_command(journal):
    journal.record_command(command())
    journal.mark_delivered("j1")
    journal.add_event(event("e1", kind="running", anchor="turn-1"))
    result = {"text": "done", "documents": [], "truncated": False}
    journal.add_event(event("e2", kind="succeeded", result=result))
    assert journal.command_state("j1") == "finished"
    assert journal.unacked_events()[1]["result"] == result
    assert journal.counts() == {"pending_commands": 0, "outbox": 2}


@pytest.mark.parametrize("setup", ["unknown", "pending"])
def test_events_for_jobs_the_browser_never_got_are_refused(journal, setup):
    if setup == "pending":
        journal.record_command(command())
    with pytest.raises(ValueError, match="unknown_job"):
        journal.add_event(event())
    assert journal.unacked_events() == []


@pytest.mark.parametrize("bad", [event(kind="done"), event(event_id=""), {"kind": "running"}])
def test_malformed_events_are_refused(journal, bad):
    with pytest.raises(ValueError, match="invalid_event"):
        journal.add_event(bad)


def test_oversized_event_is_refused_instead_of_blocking_the_outbox(journal):
    journal.record_command(command())
    journal.mark_delivered("j1")
    huge = {"text": "x" * 300_000, "documents": [], "truncated": False}
    with pytest.raises(ValueError, match="event_too_large"):
        journal.add_event(event(kind="succeeded", result=huge))
    assert journal.command_state("j1") == "delivered"
