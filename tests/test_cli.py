"""Unit tests for CLI helper functions in persfin.cli.

Only pure helper functions that do not require a live server, browser, or
interactive terminal are tested here.  Functions that orchestrate threads and
user input (_start_server_thread, _wait_for_new_session, main, …) are
integration concerns and are not covered at unit-test level.
"""

import csv
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import typer

from persfin.cli import (
    PocketSmithSyncConfig,
    _cache_key,
    _export_transactions_to_csv,
    _load_session_cache,
    _pocketsmith_start_date,
    _print_pocketsmith_result,
    _save_session_cache,
    _validate_from_date,
    _write_debug_csv,
)
from persfin.schemas.schemas import (
    AccountIdentification,
    AccountRef,
    BalancesResponse,
    BankSession,
    SessionResponse,
    Transaction,
    TransactionsResponse,
)
from persfin.services.pocketsmith import (
    PocketSmithClient,
    PocketSmithSyncResult,
    PocketSmithTransaction,
)

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
    def test_returns_empty_dict_when_file_missing(self, cache_file: Path) -> None:
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
        key2 = _cache_key(
            expired_bank_session.aspsp_name, expired_bank_session.aspsp_country
        )
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
        key2 = _cache_key(
            expired_bank_session.aspsp_name, expired_bank_session.aspsp_country
        )

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


# ── _validate_from_date ────────────────────────────────────────────────────────


class TestValidateFromDate:
    def test_valid_past_date_succeeds(self) -> None:
        assert _validate_from_date("2023-12-31") == date(2023, 12, 31)

    def test_today_date_succeeds(self) -> None:
        today_date = date.today()
        today_str = today_date.isoformat()
        assert _validate_from_date(today_str) == today_date

    def test_none_succeeds(self) -> None:
        assert _validate_from_date(None) is None

    def test_future_date_fails(self) -> None:
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        with pytest.raises(typer.BadParameter, match="Date must be today or earlier"):
            _validate_from_date(tomorrow)

    def test_invalid_format_fails(self) -> None:
        with pytest.raises(typer.BadParameter, match="Must be YYYY-MM-DD"):
            _validate_from_date("2023/12/31")
        with pytest.raises(typer.BadParameter, match="Must be YYYY-MM-DD"):
            _validate_from_date("31-12-2023")

    def test_invalid_calendar_date_fails(self) -> None:
        with pytest.raises(typer.BadParameter, match="Must be YYYY-MM-DD"):
            _validate_from_date("2023-02-30")


class TestPocketSmithStartDate:
    def test_uses_cutover_without_cli_date(self) -> None:
        cutover = date(2026, 9, 1)

        assert _pocketsmith_start_date(None, cutover, date(2026, 6, 1)) == cutover

    def test_uses_later_of_cli_and_cutover_dates(self) -> None:
        cutover = date(2026, 9, 1)

        default = date(2026, 6, 1)

        assert _pocketsmith_start_date(date(2026, 9, 10), cutover, default) == date(
            2026, 9, 10
        )
        assert _pocketsmith_start_date(date(2026, 8, 1), cutover, default) == cutover

    def test_uses_cli_date_when_cutover_is_not_configured(self) -> None:
        requested = date(2026, 9, 10)

        assert _pocketsmith_start_date(requested, None, date(2026, 6, 1)) == requested

    def test_uses_default_when_no_dates_are_configured(self) -> None:
        default = date(2026, 6, 1)

        assert _pocketsmith_start_date(None, None, default) == default


class TestPocketSmithOutput:
    def test_prints_stored_count_and_date_range(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _print_pocketsmith_result(
            PocketSmithSyncResult(
                created=3,
                first_created_date=date(2026, 9, 5),
                last_created_date=date(2026, 9, 18),
            )
        )

        assert (
            "PocketSmith: 3 stored (first date: 2026-09-05, "
            "last date: 2026-09-18)" in capsys.readouterr().out
        )

    def test_prints_zero_count_without_dates(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _print_pocketsmith_result(PocketSmithSyncResult())

        assert (
            "PocketSmith: 0 stored (first date: n/a, last date: n/a)"
            in capsys.readouterr().out
        )

    def test_empty_source_account_still_prints_zero_result(
        self, tmp_path: Path, mocker, capsys: pytest.CaptureFixture[str]
    ) -> None:
        account = AccountRef(
            uid="source-uid",
            account_id=AccountIdentification(iban="NO11111111111"),
        )
        session = SessionResponse(session_id="session", accounts=[account])
        mocker.patch(
            "persfin.cli.get_balances", return_value=BalancesResponse(balances=[])
        )
        mocker.patch(
            "persfin.cli.get_transactions",
            return_value=TransactionsResponse(transactions=[]),
        )
        sync_config = PocketSmithSyncConfig(
            client=mocker.Mock(spec=PocketSmithClient),
            mappings={"NO11111111111": 42},
        )

        _export_transactions_to_csv(
            [session], output_dir=tmp_path, pocketsmith=sync_config
        )

        assert (
            "PocketSmith: 0 stored (first date: n/a, last date: n/a)"
            in capsys.readouterr().out
        )

    def test_csv_uses_enriched_transaction_description(
        self, tmp_path: Path, mocker
    ) -> None:
        account = AccountRef(
            uid="source-uid",
            account_id=AccountIdentification(iban="NO11111111111"),
        )
        transaction = Transaction.model_validate(
            {
                "booking_date": "2026-09-13",
                "transaction_amount": {"amount": "2457.00", "currency": "NOK"},
                "credit_debit_indicator": "DBIT",
                "status": "BOOK",
                "remittance_information": ["Lønn"],
                "creditor_account": {
                    "other": {"identification": "95231670387"}
                },
            }
        )
        mocker.patch(
            "persfin.cli.get_balances", return_value=BalancesResponse(balances=[])
        )
        mocker.patch(
            "persfin.cli.get_transactions",
            return_value=TransactionsResponse(transactions=[transaction]),
        )

        _export_transactions_to_csv(
            [SessionResponse(session_id="session", accounts=[account])],
            from_date=date(2026, 9, 1),
            output_dir=tmp_path,
        )

        with (tmp_path / "NO11111111111.csv").open(
            encoding="utf-8", newline=""
        ) as csv_file:
            row = next(csv.DictReader(csv_file))
        assert row["remittance_information"] == "Lønn | 95231670387"


class TestDebugCsv:
    def test_writes_all_fields_and_json_encodes_nested_values(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "account_debug.csv"
        records = [
            {
                "transaction_id": "bank-1",
                "remittance_information": ["Invoice", "123"],
                "transaction_amount": {
                    "amount": "10.00",
                    "currency": "NOK",
                    "bank_extension": "retained",
                },
                "unknown_bank_field": {"nested": True},
                "nullable_field": None,
                "marker_string": "<field not returned>",
            },
            {"transaction_id": ""},
        ]

        _write_debug_csv(records, path)

        with path.open(encoding="utf-8", newline="") as csv_file:
            rows = list(csv.DictReader(csv_file))
        assert json.loads(rows[0]["transaction_id"]) == "bank-1"
        assert json.loads(rows[0]["remittance_information"]) == ["Invoice", "123"]
        assert json.loads(rows[0]["transaction_amount"])["bank_extension"] == (
            "retained"
        )
        assert json.loads(rows[0]["unknown_bank_field"]) == {"nested": True}
        assert json.loads(rows[0]["nullable_field"]) is None
        assert json.loads(rows[0]["marker_string"]) == "<field not returned>"
        assert rows[1]["nullable_field"] == ""
        assert json.loads(rows[1]["transaction_id"]) == ""

    def test_creates_empty_file_when_api_returns_no_records(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "pocketsmith_42_debug.csv"

        _write_debug_csv([], path)

        assert path.read_text(encoding="utf-8") == ""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX file permissions are not enforced on Windows",
    )
    def test_debug_file_permissions_are_owner_only(self, tmp_path: Path) -> None:
        import stat

        path = tmp_path / "account_debug.csv"

        _write_debug_csv([{"id": 1}], path)

        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_export_writes_enable_banking_and_pocketsmith_debug_files(
        self, tmp_path: Path, mocker
    ) -> None:
        account = AccountRef(
            uid="source-uid",
            account_id=AccountIdentification(iban="NO11111111111"),
        )
        session = SessionResponse(session_id="session", accounts=[account])
        transaction = Transaction.model_validate(
            {
                "transaction_id": "bank-1",
                "booking_date": "2026-09-18",
                "transaction_amount": {"amount": "10.00", "currency": "NOK"},
                "credit_debit_indicator": "DBIT",
                "status": "BOOK",
                "bank_extension": {"source": "retained"},
            }
        )
        pocketsmith_transaction = PocketSmithTransaction.model_validate(
            {
                "id": 99,
                "memo": "persfin:existing",
                "payee": "Existing transaction",
                "labels": ["debug"],
            }
        )
        mocker.patch(
            "persfin.cli.get_balances", return_value=BalancesResponse(balances=[])
        )
        mocker.patch(
            "persfin.cli.get_transactions",
            return_value=TransactionsResponse(transactions=[transaction]),
        )

        def fake_sync_transactions(**kwargs):
            callback = kwargs["existing_transactions_callback"]
            callback([pocketsmith_transaction])
            return PocketSmithSyncResult(
                existing_transactions=(pocketsmith_transaction,)
            )

        mocker.patch(
            "persfin.cli.sync_transactions", side_effect=fake_sync_transactions
        )
        sync_config = PocketSmithSyncConfig(
            client=mocker.Mock(spec=PocketSmithClient),
            mappings={"NO11111111111": 42},
        )

        _export_transactions_to_csv(
            [session],
            from_date=date(2026, 9, 1),
            output_dir=tmp_path,
            pocketsmith=sync_config,
            debug=True,
        )

        with (tmp_path / "NO11111111111_debug.csv").open(
            encoding="utf-8", newline=""
        ) as csv_file:
            enable_banking_rows = list(csv.DictReader(csv_file))
        with (tmp_path / "pocketsmith_42_debug.csv").open(
            encoding="utf-8", newline=""
        ) as csv_file:
            pocketsmith_rows = list(csv.DictReader(csv_file))

        assert json.loads(enable_banking_rows[0]["bank_extension"]) == {
            "source": "retained"
        }
        assert json.loads(pocketsmith_rows[0]["payee"]) == "Existing transaction"
        assert json.loads(pocketsmith_rows[0]["labels"]) == ["debug"]

    def test_successful_empty_response_clears_account_debug_file(
        self, tmp_path: Path, mocker
    ) -> None:
        debug_path = tmp_path / "NO11111111111_debug.csv"
        debug_path.write_text("stale data", encoding="utf-8")
        account = AccountRef(
            uid="source-uid",
            account_id=AccountIdentification(iban="NO11111111111"),
        )
        session = SessionResponse(session_id="session", accounts=[account])
        mocker.patch(
            "persfin.cli.get_balances", return_value=BalancesResponse(balances=[])
        )
        mocker.patch(
            "persfin.cli.get_transactions",
            return_value=TransactionsResponse(transactions=[]),
        )

        _export_transactions_to_csv([session], output_dir=tmp_path, debug=True)

        assert debug_path.read_text(encoding="utf-8") == ""

    def test_later_page_failure_preserves_received_debug_records(
        self, tmp_path: Path, mocker
    ) -> None:
        account = AccountRef(
            uid="source-uid",
            account_id=AccountIdentification(iban="NO11111111111"),
        )
        session = SessionResponse(session_id="session", accounts=[account])
        transaction = Transaction.model_validate(
            {
                "transaction_id": "first-page",
                "transaction_amount": {"amount": "10.00", "currency": "NOK"},
            }
        )
        mocker.patch(
            "persfin.cli.get_balances", return_value=BalancesResponse(balances=[])
        )
        mocker.patch(
            "persfin.cli.get_transactions",
            side_effect=[
                TransactionsResponse(
                    transactions=[transaction], continuation_key="next-page"
                ),
                RuntimeError("second page failed"),
            ],
        )

        _export_transactions_to_csv([session], output_dir=tmp_path, debug=True)

        with (tmp_path / "NO11111111111_debug.csv").open(
            encoding="utf-8", newline=""
        ) as csv_file:
            rows = list(csv.DictReader(csv_file))
        assert json.loads(rows[0]["transaction_id"]) == "first-page"

    def test_debug_write_failure_does_not_suppress_normal_csv(
        self, tmp_path: Path, mocker, capsys: pytest.CaptureFixture[str]
    ) -> None:
        account = AccountRef(
            uid="source-uid",
            account_id=AccountIdentification(iban="NO11111111111"),
        )
        session = SessionResponse(session_id="session", accounts=[account])
        transaction = Transaction.model_validate(
            {
                "transaction_id": "bank-1",
                "booking_date": "2026-09-18",
                "transaction_amount": {"amount": "10.00", "currency": "NOK"},
                "credit_debit_indicator": "DBIT",
                "status": "BOOK",
            }
        )
        mocker.patch(
            "persfin.cli.get_balances", return_value=BalancesResponse(balances=[])
        )
        mocker.patch(
            "persfin.cli.get_transactions",
            return_value=TransactionsResponse(transactions=[transaction]),
        )
        mocker.patch("persfin.cli._write_debug_csv", side_effect=OSError("disk full"))

        _export_transactions_to_csv([session], output_dir=tmp_path, debug=True)

        assert (tmp_path / "NO11111111111.csv").exists()
        assert "Could not write Enable Banking debug CSV" in capsys.readouterr().out

    def test_first_page_failure_removes_stale_debug_file(
        self, tmp_path: Path, mocker
    ) -> None:
        debug_path = tmp_path / "NO11111111111_debug.csv"
        debug_path.write_text("stale data", encoding="utf-8")
        account = AccountRef(
            uid="source-uid",
            account_id=AccountIdentification(iban="NO11111111111"),
        )
        session = SessionResponse(session_id="session", accounts=[account])
        mocker.patch(
            "persfin.cli.get_balances", return_value=BalancesResponse(balances=[])
        )
        mocker.patch(
            "persfin.cli.get_transactions", side_effect=RuntimeError("API unavailable")
        )

        _export_transactions_to_csv([session], output_dir=tmp_path, debug=True)

        assert not debug_path.exists()
