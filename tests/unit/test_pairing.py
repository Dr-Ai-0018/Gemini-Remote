import pytest

from gemini_remote.storage.pairing import (
    CODE_TTL_SECONDS,
    MAX_CODE_FAILURES,
    PairingError,
    PairingStore,
    is_extension_origin,
    read_pairing,
)

NOW = 1_000_000.0
ORIGIN = "chrome-extension://" + "a" * 32


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = PairingStore(tmp_path / "relay.sqlite3", clock)
    yield s
    s.close()


def wrong(code):
    return "000000" if code != "000000" else "111111"


def test_fresh_store_is_unpaired(store):
    assert store.status() == {"paired": False, "origin": None, "paired_at": None, "code_active": False}
    assert not store.check("anything", ORIGIN)


def test_code_buys_a_token_bound_to_the_origin(store):
    code = store.new_code()
    assert len(code) == 6 and code.isdigit()
    token = store.redeem(f" {code} ", ORIGIN)  # stray spaces from copy-paste are fine
    assert store.check(token, ORIGIN)
    assert not store.check(token, "chrome-extension://" + "b" * 32)
    assert not store.check(token + "x", ORIGIN)
    assert store.status() == {"paired": True, "origin": ORIGIN, "paired_at": NOW, "code_active": False}


def test_code_is_single_use(store):
    code = store.new_code()
    store.redeem(code, ORIGIN)
    with pytest.raises(PairingError, match="no_code"):
        store.redeem(code, ORIGIN)


def test_code_expires(store, clock):
    code = store.new_code()
    clock.now += CODE_TTL_SECONDS
    with pytest.raises(PairingError, match="no_code"):
        store.redeem(code, ORIGIN)


def test_new_code_replaces_the_old_one(store):
    first = store.new_code()
    second = store.new_code()
    if first != second:
        with pytest.raises(PairingError, match="bad_code"):
            store.redeem(first, ORIGIN)
    store.redeem(second, ORIGIN)


def test_code_burns_after_too_many_wrong_guesses(store):
    code = store.new_code()
    for _ in range(MAX_CODE_FAILURES):
        with pytest.raises(PairingError, match="bad_code"):
            store.redeem(wrong(code), ORIGIN)
    with pytest.raises(PairingError, match="no_code"):
        store.redeem(code, ORIGIN)


@pytest.mark.parametrize("code", [None, 123456, ""])
def test_malformed_codes_count_as_wrong(store, code):
    store.new_code()
    with pytest.raises(PairingError, match="bad_code"):
        store.redeem(code, ORIGIN)


def test_repairing_replaces_the_old_extension(store):
    old = store.redeem(store.new_code(), ORIGIN)
    other = "chrome-extension://" + "b" * 32
    new = store.redeem(store.new_code(), other)
    assert not store.check(old, ORIGIN)
    assert store.check(new, other)


def test_reset_forgets_pairing_and_code(store):
    token = store.redeem(store.new_code(), ORIGIN)
    code = store.new_code()
    store.reset()
    assert not store.check(token, ORIGIN)
    with pytest.raises(PairingError, match="no_code"):
        store.redeem(code, ORIGIN)


def test_state_is_shared_between_processes(tmp_path, clock):
    cli = PairingStore(tmp_path / "relay.sqlite3", clock)
    relay = PairingStore(tmp_path / "relay.sqlite3", clock)
    token = relay.redeem(cli.new_code(), ORIGIN)
    cli.reset()
    assert not relay.check(token, ORIGIN)
    cli.close()
    relay.close()


def test_only_hashes_are_stored(tmp_path, clock):
    store = PairingStore(tmp_path / "relay.sqlite3", clock)
    code = store.new_code()
    token = store.redeem(code, ORIGIN)
    store.close()
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("relay.sqlite3*"))
    assert token.encode() not in raw and f"{code}".encode() not in raw


@pytest.mark.parametrize("origin", [None, "", "https://gemini.google.com", "chrome-extension://" + "a" * 31,
                                    "chrome-extension://" + "z" * 32, "chrome-extension://" + "a" * 32 + "/"])
def test_only_well_formed_extension_origins(store, origin):
    assert not is_extension_origin(origin)
    store_code = store.new_code()
    with pytest.raises(PairingError, match="bad_origin"):
        store.redeem(store_code, origin)


def test_read_pairing_for_doctor(tmp_path, store):
    assert read_pairing(tmp_path / "missing.sqlite3") is None
    assert read_pairing(tmp_path / "relay.sqlite3")["paired"] is False
    store.redeem(store.new_code(), ORIGIN)
    assert read_pairing(tmp_path / "relay.sqlite3") == {"paired": True, "origin": ORIGIN, "paired_at": NOW}
