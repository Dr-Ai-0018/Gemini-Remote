import pytest

from gemini_remote import __version__
from gemini_remote.cli.main import main
from gemini_remote.config.settings import Settings


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_init_local_creates_usable_env_without_printing_tokens(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["init-local"])
    env_file = tmp_path / ".env"
    content = env_file.read_text(encoding="utf-8")
    output = capsys.readouterr()
    for role in ("gateway", "relay", "client"):
        Settings.from_env(role, environ={}, env_file=env_file)
    relay = Settings.from_env("relay", environ={}, env_file=env_file)
    assert relay.extension_origin.startswith("chrome-extension://")
    assert "PRIVATE" not in content
    for line in content.splitlines():
        if "_TOKEN=" in line or "_KEY=" in line:
            secret = line.split("=", 1)[1]
            assert secret not in output.out + output.err


def test_each_init_local_gets_its_own_extension_identity(tmp_path, monkeypatch):
    origins = set()
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        monkeypatch.chdir(tmp_path / name)
        main(["init-local"])
        origins.add(Settings.from_env("relay", environ={}, env_file=tmp_path / name / ".env").extension_origin)
    assert len(origins) == 2


def test_gateway_refuses_to_start_without_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in [k for k in __import__("os").environ if k.startswith("GEMINI_REMOTE_")]:
        monkeypatch.delenv(key)
    with pytest.raises(SystemExit) as exc:
        main(["gateway"])
    assert exc.value.code == 2
    assert "CONTROL_TOKEN" in capsys.readouterr().err


def test_relay_refuses_to_start_without_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in [k for k in __import__("os").environ if k.startswith("GEMINI_REMOTE_")]:
        monkeypatch.delenv(key)
    with pytest.raises(SystemExit) as exc:
        main(["relay"])
    assert exc.value.code == 2
    assert "GEMINI_REMOTE_" in capsys.readouterr().err


def test_doctor_on_a_fresh_install_reports_the_relay_is_not_running(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in [k for k in __import__("os").environ if k.startswith("GEMINI_REMOTE_")]:
        monkeypatch.delenv(key)
    main(["init-local"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        main(["doctor"])
    out = capsys.readouterr().out
    assert exc.value.code == 1
    assert "relay    ok" in out and "relay: not running" in out


def test_init_local_refuses_to_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("keep me\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["init-local"])
    assert exc.value.code == 2
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "keep me\n"
