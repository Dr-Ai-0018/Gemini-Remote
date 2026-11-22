from importlib.metadata import version

import gemini_remote
from gemini_remote.contracts.models import DEFAULT_SEND_MODE, TERMINAL_STATUSES, JobStatus, SendMode


def test_package_version_matches_metadata():
    assert gemini_remote.__version__ == version("gemini-remote")


def test_unconfirmed_is_not_terminal_so_reconcile_can_still_resolve_it():
    assert JobStatus.UNCONFIRMED not in TERMINAL_STATUSES
    assert TERMINAL_STATUSES == {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMED_OUT}


def test_status_values_are_wire_strings():
    assert JobStatus("timed_out") is JobStatus.TIMED_OUT


def test_default_send_mode_is_task():
    assert DEFAULT_SEND_MODE is SendMode.TASK
