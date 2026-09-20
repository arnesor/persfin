from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from persfin.cli import _cache_key, _save_session_cache, cli_app
from persfin.core.cli_config import (
    CliConfig,
    PocketSmithMapping,
    SelectedAccount,
    SelectedBank,
    save_cli_config,
)
from persfin.schemas.schemas import BankSession

runner = CliRunner()


def _configure_paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "config.json"
    cache_path = tmp_path / "sessions.json"
    monkeypatch.setattr("persfin.cli._CONFIG_FILE", config_path)
    monkeypatch.setattr("persfin.cli._CACHE_FILE", cache_path)
    monkeypatch.setattr("persfin.cli._CACHE_DIR", tmp_path)
    return config_path, cache_path


def _config() -> CliConfig:
    config = CliConfig()
    config.enable_banking.banks = [
        SelectedBank(
            aspsp_name="TestBank",
            aspsp_country="NO",
            accounts=[
                SelectedAccount(
                    key="iban:NO11111111111",
                    iban="NO11111111111",
                    uid="uid",
                    name="Daily",
                    currency="NOK",
                )
            ],
        )
    ]
    config.pocketsmith.mappings["NO11111111111"] = PocketSmithMapping(
        status="mapped",
        transaction_account_id=42,
        transaction_account_name="Pocket Daily",
    )
    return config


def _save_session() -> None:
    session = BankSession(
        aspsp_name="TestBank",
        aspsp_country="NO",
        session_id="secret-session-id",
        accounts=[],
        valid_until=datetime.now(UTC) + timedelta(days=1),
    )
    _save_session_cache({_cache_key("TestBank", "NO"): session})


def test_list_all_hides_session_token(monkeypatch, tmp_path: Path) -> None:
    config_path, _ = _configure_paths(monkeypatch, tmp_path)
    save_cli_config(_config(), config_path)
    _save_session()

    result = runner.invoke(cli_app, ["config", "list"])

    assert result.exit_code == 0
    assert "TestBank (NO)" in result.stdout
    assert "NO11111111111 -> Pocket Daily (id: 42)" in result.stdout
    assert "secret-session-id" not in result.stdout


def test_clear_sessions_preserves_configuration(monkeypatch, tmp_path: Path) -> None:
    config_path, cache_path = _configure_paths(monkeypatch, tmp_path)
    save_cli_config(_config(), config_path)
    _save_session()

    result = runner.invoke(cli_app, ["config", "clear", "sessions"])

    assert result.exit_code == 0
    assert config_path.exists()
    assert not cache_path.exists()


def test_clear_enablebanking_preserves_sessions_and_mappings(
    monkeypatch, tmp_path: Path
) -> None:
    config_path, cache_path = _configure_paths(monkeypatch, tmp_path)
    save_cli_config(_config(), config_path)
    _save_session()

    result = runner.invoke(cli_app, ["config", "clear", "enablebanking"])

    assert result.exit_code == 0
    assert cache_path.exists()
    listed = runner.invoke(cli_app, ["config", "list", "pocketsmith"])
    assert "NO11111111111 -> Pocket Daily (id: 42)" in listed.stdout


def test_clear_all_removes_configuration_and_sessions(
    monkeypatch, tmp_path: Path
) -> None:
    config_path, cache_path = _configure_paths(monkeypatch, tmp_path)
    save_cli_config(_config(), config_path)
    _save_session()

    result = runner.invoke(cli_app, ["config", "clear", "all"])

    assert result.exit_code == 0
    assert not config_path.exists()
    assert not cache_path.exists()


def test_clear_missing_sessions_succeeds(monkeypatch, tmp_path: Path) -> None:
    _configure_paths(monkeypatch, tmp_path)

    result = runner.invoke(cli_app, ["config", "clear", "sessions"])

    assert result.exit_code == 0
    assert "No Enable Banking authentication sessions" in result.stdout


def test_list_malformed_sessions_does_not_expose_contents(
    monkeypatch, tmp_path: Path
) -> None:
    _, cache_path = _configure_paths(monkeypatch, tmp_path)
    cache_path.write_text(
        '{"bank": {"session_id": "recognizable-secret"}}', encoding="utf-8"
    )

    result = runner.invoke(cli_app, ["config", "list", "sessions"])

    assert result.exit_code == 0
    assert "recognizable-secret" not in result.stdout
    assert "Could not read session cache" in result.stdout
