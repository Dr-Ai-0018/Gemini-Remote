from pathlib import Path

import pytest

from gemini_remote.config.settings import Settings

GOOD = "x" * 40


def relay_env(**overrides):
    env = {
        "GEMINI_REMOTE_DEVICE_TOKEN": GOOD,
        "GEMINI_REMOTE_SIGNING_KEY": GOOD,
        "GEMINI_REMOTE_BROWSER_TOKEN": GOOD,
    }
    env.update(overrides)
    return env


def test_relay_loads_without_control_token(tmp_path):
    settings = Settings.from_env("relay", environ=relay_env(), env_file=tmp_path / ".env")
    assert settings.control_token == ""
    assert settings.gateway_url.endswith(":18797/ws/device")
    assert settings.data_dir == Path(".data")


@pytest.mark.parametrize("value", ["", "short", "replace-" + "x" * 40])
def test_missing_short_or_placeholder_secret_is_rejected(tmp_path, value):
    with pytest.raises(ValueError, match="GEMINI_REMOTE_BROWSER_TOKEN"):
        Settings.from_env("relay", environ=relay_env(GEMINI_REMOTE_BROWSER_TOKEN=value),
                          env_file=tmp_path / ".env")


def test_gateway_requires_control_token(tmp_path):
    with pytest.raises(ValueError, match="CONTROL_TOKEN"):
        Settings.from_env("gateway", environ=relay_env(), env_file=tmp_path / ".env")


def test_environment_overrides_file_and_foreign_keys_are_ignored(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        f"GEMINI_REMOTE_CONTROL_TOKEN={'f' * 40}\n"
        "GEMINI_REMOTE_DEVICE_ID=from-file\n"
        "DOTS_REMOTE_DEVICE_ID=other-project\n",
        encoding="utf-8",
    )
    settings = Settings.from_env("client", environ={"GEMINI_REMOTE_DEVICE_ID": "from-env"},
                                 env_file=env_file)
    assert settings.control_token == "f" * 40
    assert settings.device_id == "from-env"


def test_unknown_role_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown role"):
        Settings.from_env("browser", environ={}, env_file=tmp_path / ".env")
