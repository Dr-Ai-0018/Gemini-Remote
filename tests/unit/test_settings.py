from pathlib import Path

import pytest

from gemini_remote.config.settings import Settings, extension_id_from_key
from gemini_remote.contracts.models import SendMode

GOOD = "x" * 40
# Throwaway key; the same vector is checked by the extension build test.
TEST_KEY = "Z2VtaW5pLXJlbW90ZS10ZXN0LWtleQ=="
TEST_ID = "adjpkjhmnhmgipgnbfjejnfkbhknkbke"


def relay_env(**overrides):
    env = {
        "GEMINI_REMOTE_DEVICE_TOKEN": GOOD,
        "GEMINI_REMOTE_SIGNING_KEY": GOOD,
        "GEMINI_REMOTE_BROWSER_TOKEN": GOOD,
        "GEMINI_REMOTE_EXTENSION_KEY": TEST_KEY,
    }
    env.update(overrides)
    return env


def test_relay_loads_without_control_token(tmp_path):
    settings = Settings.from_env("relay", environ=relay_env(), env_file=tmp_path / ".env")
    assert settings.control_token == ""
    assert settings.gateway_url.endswith(":18797/ws/device")
    assert settings.relay_port == 18776
    assert settings.data_dir == Path(".data")
    assert settings.default_mode is SendMode.TASK
    assert settings.extension_origin == f"chrome-extension://{TEST_ID}"


def test_extension_id_matches_chromium_derivation():
    assert extension_id_from_key(TEST_KEY) == TEST_ID


def test_relay_requires_extension_key(tmp_path):
    env = relay_env()
    del env["GEMINI_REMOTE_EXTENSION_KEY"]
    with pytest.raises(ValueError, match="EXTENSION_KEY"):
        Settings.from_env("relay", environ=env, env_file=tmp_path / ".env")


@pytest.mark.parametrize(
    ("name", "value"),
    [("GEMINI_REMOTE_RELAY_PORT", "70000"), ("GEMINI_REMOTE_RELAY_PORT", "abc"),
     ("GEMINI_REMOTE_DEFAULT_MODE", "deep"), ("GEMINI_REMOTE_EXTENSION_KEY", "not base64!")],
)
def test_invalid_local_settings_are_rejected(tmp_path, name, value):
    with pytest.raises(ValueError, match=name):
        Settings.from_env("relay", environ=relay_env(**{name: value}), env_file=tmp_path / ".env")


def test_local_preferences_override_defaults(tmp_path):
    settings = Settings.from_env(
        "relay",
        environ=relay_env(GEMINI_REMOTE_RELAY_PORT="23456", GEMINI_REMOTE_DEFAULT_MODE="chat"),
        env_file=tmp_path / ".env",
    )
    assert settings.relay_port == 23456
    assert settings.default_mode is SendMode.CHAT


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
        "OTHER_APP_DEVICE_ID=other-project\n",
        encoding="utf-8",
    )
    settings = Settings.from_env("client", environ={"GEMINI_REMOTE_DEVICE_ID": "from-env"},
                                 env_file=env_file)
    assert settings.control_token == "f" * 40
    assert settings.device_id == "from-env"


def test_unknown_role_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown role"):
        Settings.from_env("browser", environ={}, env_file=tmp_path / ".env")
