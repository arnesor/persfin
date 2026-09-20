import json
import stat
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from persfin.core.cli_config import (
    CliConfig,
    PocketSmithMapping,
    SelectedAccount,
    SelectedBank,
    account_key,
    load_cli_config,
    normalize_account_number,
    save_cli_config,
)
from persfin.schemas.schemas import AccountIdentification, AccountRef


def test_normalizes_account_number() -> None:
    assert normalize_account_number("no11 1111 11111") == "NO11111111111"


def test_account_key_prefers_iban_then_hash_then_uid() -> None:
    assert (
        account_key(
            AccountRef(
                uid="uid",
                account_id=AccountIdentification(iban="no11 1111 11111"),
                identification_hash="hash",
            )
        )
        == "iban:NO11111111111"
    )
    assert account_key(AccountRef(uid="uid", identification_hash="hash")) == "hash:hash"
    assert account_key(AccountRef(uid="uid")) == "uid:uid"


def test_selected_account_captures_display_metadata() -> None:
    selected = SelectedAccount.from_account(
        AccountRef(
            uid="uid",
            account_id=AccountIdentification(iban="no11 1111 11111"),
            name="Daily",
            currency="NOK",
        )
    )

    assert selected.iban == "NO11111111111"
    assert selected.name == "Daily"
    assert selected.currency == "NOK"


def test_load_missing_returns_default(tmp_path: Path) -> None:
    assert load_cli_config(tmp_path / "missing.json") == CliConfig()


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = CliConfig()
    config.enable_banking.banks.append(
        SelectedBank(
            aspsp_name="TestBank",
            aspsp_country="NO",
            accounts=[SelectedAccount(key="uid:1", uid="1", name="Daily")],
        )
    )
    config.pocketsmith.mappings["NO11111111111"] = PocketSmithMapping(
        status="mapped",
        transaction_account_id=42,
        transaction_account_name="Daily",
    )

    save_cli_config(config, path)

    assert load_cli_config(path) == config
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "content",
    ["{invalid", json.dumps({"version": 2})],
)
def test_invalid_configuration_is_not_silently_ignored(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="Could not read CLI configuration"):
        load_cli_config(path)


def test_mapping_requires_destination_id() -> None:
    with pytest.raises(ValidationError, match="require an account ID"):
        PocketSmithMapping(status="mapped")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_saved_configuration_has_owner_only_permissions(tmp_path: Path) -> None:
    path = tmp_path / "config.json"

    save_cli_config(CliConfig(), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
