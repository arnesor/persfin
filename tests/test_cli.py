"""Unit tests for CLI helper functions in persfin.cli.

Only pure helper functions that do not require a live server, browser, or
interactive terminal are tested here.  Functions that orchestrate threads and
user input (_start_server_thread, _wait_for_new_session, main, …) are
integration concerns and are not covered at unit-test level.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from persfin.cli import _cache_key, _load_session_cache, _save_session_cache
from persfin.models import AccountRef, BankSession


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def bank_session(fake_account: AccountRef) -> BankSession:
    """A valid (non-expired) BankSession for testing."""
    return BankSession(
        aspsp_name="TestBank",
        aspsp_country="NO",
        session_id="sess-999",
        accounts=[fake_account],
        valid_until=datetime.now(UTC) + timedelta(days=90),
    )


@pytest.fixture()
def expired_bank_session(fake_account: AccountRef) -> BankSession:
    """An expired BankSession for testing."""
    return BankSession(
        aspsp_name="OldBank",
        aspsp_country="NO",
        session_id="sess-old",
        accounts=[fake_account],
        valid_until=datetime.now(UTC) - timedelta(days=1),
    )


@pytest.fixture()
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect cache constants to a temporary directory and return the file path."""
    monkeypatch.setattr("persfin.cli._CACHE_DIR", tmp_path)
    monkeypatch.setattr("persfin.cli._CACHE_FILE", tmp_path / "session_cache.json")
    return tmp_path / "session_cache.json"


# ── _cache_key ────────────────────────────────────────────────────────────────


class TestCacheKey:
    def test_format_is_name_pipe_country(self) -> None:
        assert _cache_key("DNB Bank", "NO") == "DNB Bank|NO"

    def test_different_names_produce_different_keys(self) -> None:
        assert _cache_key("Sbanken", "NO") != _cache_key("DNB Bank", "NO")

    def test_same_name_different_country_differs(self) -> None:
        assert _cache_key("Bank", "NO") != _cache_key("Bank", "SE")

    def test_same_inputs_produce_same_key(self) -> None:
        assert _cache_key("TestBank", "NO") == _cache_key("TestBank", "NO")


# ── _load_session_cache ───────────────────────────────────────────────────────


class TestLoadSessionCache:
    def test_returns_empty_dict_when_file_missing(
        self, cache_file: Path
    ) -> None:
        assert not cache_file.exists()
        assert _load_session_cache() == {}

    def test_loads_valid_session_from_file(
        self, cache_file: Path, bank_session: BankSession
    ) -> None:
        key = _cache_key(bank_session.aspsp_name, bank_session.aspsp_country)
        cache_file.write_text(
            json.dumps({key: json.loads(bank_session.model_dump_json())}),
            encoding="utf-8",
        )

        result = _load_session_cache()

        assert key in result
        assert result[key].session_id == bank_session.session_id
        assert result[key].aspsp_name == bank_session.aspsp_name
        assert result[key].aspsp_country == bank_session.aspsp_country

    def test_loads_multiple_sessions(
        self,
        cache_file: Path,
        bank_session: BankSession,
        expired_bank_session: BankSession,
    ) -> None:
        key1 = _cache_key(bank_session.aspsp_name, bank_session.aspsp_country)
        key2 = _cache_key(expired_bank_session.aspsp_name, expired_bank_session.aspsp_country)
        cache_file.write_text(
            json.dumps(
                {
                    key1: json.loads(bank_session.model_dump_json()),
                    key2: json.loads(expired_bank_session.model_dump_json()),
                }
            ),
            encoding="utf-8",
        )

        result = _load_session_cache()

        assert len(result) == 2
        assert result[key1].is_valid() is True
        assert result[key2].is_valid() is False

    def test_returns_empty_dict_on_invalid_json(self, cache_file: Path) -> None:
        cache_file.write_text("{ this is not valid json", encoding="utf-8")

        result = _load_session_cache()

        assert result == {}

    def test_returns_empty_dict_on_wrong_schema(self, cache_file: Path) -> None:
        cache_file.write_text(
            json.dumps({"key": {"unexpected": "structure"}}), encoding="utf-8"
        )

        result = _load_session_cache()

        assert result == {}


# ── _save_session_cache ───────────────────────────────────────────────────────


class TestSaveSessionCache:
    def test_creates_file(self, cache_file: Path, bank_session: BankSession) -> None:
        _save_session_cache({_cache_key("TestBank", "NO"): bank_session})

        assert cache_file.exists()

    def test_writes_correct_session_id(
        self, cache_file: Path, bank_session: BankSession
    ) -> None:
        key = _cache_key(bank_session.aspsp_name, bank_session.aspsp_country)
        _save_session_cache({key: bank_session})

        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data[key]["session_id"] == bank_session.session_id

    def test_writes_correct_aspsp_fields(
        self, cache_file: Path, bank_session: BankSession
    ) -> None:
        key = _cache_key(bank_session.aspsp_name, bank_session.aspsp_country)
        _save_session_cache({key: bank_session})

        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data[key]["aspsp_name"] == "TestBank"
        assert data[key]["aspsp_country"] == "NO"

    def test_roundtrip_preserves_data(
        self, cache_file: Path, bank_session: BankSession
    ) -> None:
        key = _cache_key(bank_session.aspsp_name, bank_session.aspsp_country)
        _save_session_cache({key: bank_session})

        reloaded = _load_session_cache()

        assert reloaded[key].session_id == bank_session.session_id
        assert reloaded[key].aspsp_name == bank_session.aspsp_name
        assert reloaded[key].accounts[0].uid == bank_session.accounts[0].uid

    def test_overwrites_previous_cache(
        self,
        cache_file: Path,
        bank_session: BankSession,
        expired_bank_session: BankSession,
    ) -> None:
        key1 = _cache_key(bank_session.aspsp_name, bank_session.aspsp_country)
        key2 = _cache_key(expired_bank_session.aspsp_name, expired_bank_session.aspsp_country)

        _save_session_cache({key1: bank_session})
        _save_session_cache({key2: expired_bank_session})  # second write replaces first

        result = _load_session_cache()
        assert key1 not in result
        assert key2 in result

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX file permissions are not enforced on Windows",
    )
    def test_file_permissions_are_owner_only(
        self, cache_file: Path, bank_session: BankSession
    ) -> None:
        import stat

        _save_session_cache({_cache_key("TestBank", "NO"): bank_session})

        file_mode = stat.S_IMODE(cache_file.stat().st_mode)
        assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX file permissions are not enforced on Windows",
    )
    def test_directory_permissions_are_owner_only(
        self, tmp_path: Path, cache_file: Path, bank_session: BankSession
    ) -> None:
        import stat

        _save_session_cache({_cache_key("TestBank", "NO"): bank_session})

        dir_mode = stat.S_IMODE(tmp_path.stat().st_mode)
        assert dir_mode == 0o700, f"Expected 0o700, got {oct(dir_mode)}"
