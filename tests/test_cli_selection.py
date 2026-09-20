from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from persfin.cli import (
    _configured_sessions,
    _prompt_account_multi_selection,
    _resolve_pocketsmith_mappings,
    run_cli,
)
from persfin.core.cli_config import (
    CliConfig,
    PocketSmithMapping,
    SelectedAccount,
    SelectedBank,
    load_cli_config,
    save_cli_config,
)
from persfin.core.session_store import SessionStore
from persfin.schemas.schemas import (
    AccountIdentification,
    AccountRef,
    AuthStartResult,
    BankSession,
    SessionResponse,
)
from persfin.services.pocketsmith import PocketSmithTransactionAccount


def _account(uid: str, iban: str, name: str) -> AccountRef:
    return AccountRef(
        uid=uid,
        account_id=AccountIdentification(iban=iban),
        name=name,
        currency="NOK",
    )


def test_prompts_for_multiple_accounts(monkeypatch) -> None:
    accounts = [
        _account("one", "NO11111111111", "Daily"),
        _account("two", "NO22222222222", "Savings"),
    ]
    monkeypatch.setattr("builtins.input", lambda _: "2, 1, 2")

    selected = _prompt_account_multi_selection("TestBank", accounts)

    assert [account.uid for account in selected] == ["two", "one"]


def test_configured_sessions_filter_accounts_and_warn_for_missing(
    capsys,
) -> None:
    available = _account("one", "NO11111111111", "Daily")
    bank = SelectedBank(
        aspsp_name="TestBank",
        aspsp_country="NO",
        accounts=[
            SelectedAccount.from_account(available),
            SelectedAccount(
                key="iban:NO99999999999",
                iban="NO99999999999",
                uid="missing",
            ),
        ],
    )
    session = BankSession(
        aspsp_name="TestBank",
        aspsp_country="NO",
        session_id="session",
        accounts=[available, _account("two", "NO22222222222", "Savings")],
        valid_until=datetime.now(UTC) + timedelta(days=1),
    )

    sessions = _configured_sessions([bank], {"TestBank|NO": session})

    assert [account.uid for account in sessions[0].accounts] == ["one"]
    assert "NO99999999999" in capsys.readouterr().out


def test_configured_account_falls_back_to_identification_hash() -> None:
    configured = SelectedAccount(
        key="iban:NO11111111111",
        iban="NO11111111111",
        identification_hash="stable-hash",
        uid="old-uid",
    )
    refreshed = AccountRef(
        uid="new-uid", identification_hash="stable-hash", name="Daily"
    )
    bank = SelectedBank(
        aspsp_name="TestBank",
        aspsp_country="NO",
        accounts=[configured],
    )
    session = BankSession(
        aspsp_name="TestBank",
        aspsp_country="NO",
        session_id="session",
        accounts=[refreshed],
        valid_until=datetime.now(UTC) + timedelta(days=1),
    )

    result = _configured_sessions([bank], {"TestBank|NO": session})

    assert result[0].accounts == [refreshed]


def test_resolves_and_persists_mapped_and_skipped_accounts_once(
    monkeypatch, mocker, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("persfin.cli._CONFIG_FILE", config_path)
    answers = iter(["1", "0"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    client = mocker.Mock()
    client.list_transaction_accounts.return_value = [
        PocketSmithTransactionAccount(
            id=42, name="Pocket Daily", number=None, currency_code="NOK"
        )
    ]
    sessions = [
        SessionResponse(
            session_id="session",
            accounts=[
                _account("one", "NO11111111111", "Daily"),
                _account("two", "NO22222222222", "Savings"),
            ],
        )
    ]
    config = CliConfig()

    mappings = _resolve_pocketsmith_mappings(config, sessions, client)

    assert mappings == {"NO11111111111": 42}
    assert config.pocketsmith.mappings["NO22222222222"].status == "skipped"
    assert load_cli_config(config_path) == config
    client.list_transaction_accounts.assert_called_once_with()

    client.reset_mock()
    assert _resolve_pocketsmith_mappings(config, sessions, client) == mappings
    client.list_transaction_accounts.assert_not_called()


def test_rejects_removed_pocketsmith_destination(
    monkeypatch, mocker, tmp_path: Path
) -> None:
    monkeypatch.setattr("persfin.cli._CONFIG_FILE", tmp_path / "config.json")
    config = CliConfig()
    config.pocketsmith.mappings["NO11111111111"] = PocketSmithMapping(
        status="mapped",
        transaction_account_id=42,
        transaction_account_name="Removed",
    )
    sessions = [
        SessionResponse(
            session_id="session",
            accounts=[_account("one", "NO11111111111", "Daily")],
        )
    ]
    client = mocker.Mock()
    request = httpx.Request("GET", "https://api.pocketsmith.test/v2/42")
    response = httpx.Response(404, request=request)
    client.get_transaction_account.side_effect = httpx.HTTPStatusError(
        "not found", request=request, response=response
    )

    with pytest.raises(ValueError, match="config clear pocketsmith"):
        _resolve_pocketsmith_mappings(config, sessions, client)


def test_rejects_destination_shared_by_multiple_source_ibans(mocker) -> None:
    config = CliConfig()
    for iban in ("NO11111111111", "NO22222222222"):
        config.pocketsmith.mappings[iban] = PocketSmithMapping(
            status="mapped",
            transaction_account_id=42,
            transaction_account_name="Shared",
        )
    sessions = [
        SessionResponse(
            session_id="session",
            accounts=[
                _account("one", "NO11111111111", "Daily"),
                _account("two", "NO22222222222", "Savings"),
            ],
        )
    ]

    with pytest.raises(ValueError, match="only one source IBAN"):
        _resolve_pocketsmith_mappings(config, sessions, mocker.Mock())


def test_missing_session_reauthenticates_configured_bank(
    monkeypatch, mocker, tmp_path: Path
) -> None:
    account = _account("one", "NO11111111111", "Daily")
    config = CliConfig()
    config.enable_banking.banks = [
        SelectedBank(
            aspsp_name="TestBank",
            aspsp_country="NO",
            accounts=[SelectedAccount.from_account(account)],
        )
    ]
    config_path = tmp_path / "config.json"
    cache_path = tmp_path / "sessions.json"
    save_cli_config(config, config_path)
    monkeypatch.setattr("persfin.cli._CONFIG_FILE", config_path)
    monkeypatch.setattr("persfin.cli._CACHE_FILE", cache_path)
    monkeypatch.setattr("persfin.cli._CACHE_DIR", tmp_path)
    store = SessionStore()
    monkeypatch.setattr("persfin.cli.get_store", lambda: store)
    mocker.patch("persfin.cli._start_server_thread")
    start_auth = mocker.patch(
        "persfin.cli.start_auth",
        return_value=AuthStartResult(
            url="https://bank.test/auth",
            valid_until=datetime.now(UTC) + timedelta(days=90),
        ),
    )
    mocker.patch("persfin.cli.webbrowser.open")

    def finish_auth(known_ids, timeout):
        store.put(SessionResponse(session_id="new-session", accounts=[account]))
        return "new-session"

    mocker.patch("persfin.cli._wait_for_new_session", side_effect=finish_auth)
    export = mocker.patch("persfin.cli._export_transactions_to_csv")

    run_cli()

    start_auth.assert_called_once()
    assert cache_path.exists()
    assert export.call_args.args[0][0].accounts == [account]
