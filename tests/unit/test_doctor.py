import json

import pytest

from gemini_remote import __version__
from gemini_remote.cli.doctor import doctor
from gemini_remote.storage.pairing import PairingStore

GOOD = "x" * 40
ORIGIN = "chrome-extension://" + "a" * 32
NOW = 1_000_000.0


def env(tmp_path, **extra):
    values = {
        "GEMINI_REMOTE_DEVICE_TOKEN": GOOD,
        "GEMINI_REMOTE_SIGNING_KEY": GOOD,
        "GEMINI_REMOTE_DATA_DIR": str(tmp_path),
        "GEMINI_REMOTE_GATEWAY_URL": "ws://127.0.0.1:18797/ws/device",
    }
    values.update(extra)
    return values


def status(tmp_path, **overrides):
    body = {"relay_version": __version__, "updated_at": NOW - 2, "gateway_connected": True,
            "gateway_version": __version__, "browser_connected": True, "browser_ready": True,
            "extension_version": __version__, "pending_commands": 0, "outbox": 0}
    body.update(overrides)
    (tmp_path / "relay-status.json").write_text(json.dumps(body), encoding="utf-8")


def pair(tmp_path):
    store = PairingStore(tmp_path / "relay.sqlite3")
    store.redeem(store.new_code(), ORIGIN)
    store.close()


def run(tmp_path, environ):
    lines = []
    code = doctor(env_file=tmp_path / ".env", environ=environ, now=NOW, out=lines.append)
    return code, "\n".join(lines)


def test_healthy_relay(tmp_path):
    status(tmp_path)
    pair(tmp_path)
    code, report = run(tmp_path, env(tmp_path))
    assert code == 0
    assert "paired with extension " + "a" * 32 in report
    assert "relay    ok" in report and "extension: connected, ready" in report
    assert "problem" not in report
    assert "gateway  not set up on this machine" in report  # optional on the PC
    assert GOOD not in report  # never prints credentials


def test_missing_relay_settings(tmp_path):
    environ = env(tmp_path)
    del environ["GEMINI_REMOTE_DEVICE_TOKEN"]
    code, report = run(tmp_path, environ)
    assert code == 1 and "GEMINI_REMOTE_DEVICE_TOKEN" in report


@pytest.mark.parametrize(("write", "expected"), [(None, "not running"), ({"updated_at": NOW - 120}, "probably not running")])
def test_relay_not_running(tmp_path, write, expected):
    if write is not None:
        status(tmp_path, **write)
    code, report = run(tmp_path, env(tmp_path))
    assert code == 1 and expected in report


@pytest.mark.parametrize(("overrides", "expected"), [
    ({"gateway_connected": False}, "gateway: not connected"),
    ({"browser_connected": False}, "extension: not connected"),
    ({"extension_version": "0.0.1"}, "rebuild it and reload the extension"),
    ({"relay_version": "0.0.1"}, "restart it"),
])
def test_problems_are_reported(tmp_path, overrides, expected):
    status(tmp_path, **overrides)
    pair(tmp_path)
    code, report = run(tmp_path, env(tmp_path))
    assert code == 1 and expected in report


def test_unpaired_relay_says_how_to_pair(tmp_path):
    status(tmp_path)
    code, report = run(tmp_path, env(tmp_path))
    assert code == 1 and "gemini-remote pair" in report


def test_leftover_settings_are_pointed_out(tmp_path):
    status(tmp_path)
    pair(tmp_path)
    (tmp_path / ".env").write_text("GEMINI_REMOTE_EXTENSION_KEY=old\n", encoding="utf-8")
    code, report = run(tmp_path, env(tmp_path))
    assert code == 0 and "GEMINI_REMOTE_EXTENSION_KEY in .env are no longer used" in report


def test_gateway_url_path_is_not_printed(tmp_path):
    status(tmp_path)
    _, report = run(tmp_path, env(tmp_path, GEMINI_REMOTE_GATEWAY_URL="wss://gw.example.test/private/ws/device"))
    assert "wss://gw.example.test" in report and "/private" not in report
