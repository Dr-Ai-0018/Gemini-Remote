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
    for line in content.splitlines():
        if "_TOKEN=" in line or "_KEY=" in line:
            secret = line.split("=", 1)[1]
            assert secret not in output.out + output.err


def test_init_local_refuses_to_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("keep me\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["init-local"])
    assert exc.value.code == 2
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "keep me\n"
