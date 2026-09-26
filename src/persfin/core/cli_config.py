"""Persistent, non-secret configuration for the interactive CLI."""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from persfin.schemas.schemas import AccountRef


def normalize_account_number(value: str) -> str:
    """Return an account number without whitespace and in uppercase."""
    return "".join(value.split()).upper()


def account_key(account: AccountRef) -> str:
    """Return the best stable identity available for an Enable Banking account."""
    if account.account_id is not None and account.account_id.iban:
        return f"iban:{normalize_account_number(account.account_id.iban)}"
    if account.identification_hash:
        return f"hash:{account.identification_hash}"
    return f"uid:{account.uid}"


class SelectedAccount(BaseModel):
    """An Enable Banking account selected for processing."""

    model_config = ConfigDict(extra="forbid")

    key: str
    iban: str | None = None
    identification_hash: str | None = None
    uid: str
    name: str | None = None
    currency: str | None = None

    @classmethod
    def from_account(cls, account: AccountRef) -> "SelectedAccount":
        """Create a persistent selection from an API account resource."""
        iban = (
            normalize_account_number(account.account_id.iban)
            if account.account_id is not None and account.account_id.iban
            else None
        )
        return cls(
            key=account_key(account),
            iban=iban,
            identification_hash=account.identification_hash,
            uid=account.uid,
            name=account.name,
            currency=account.currency,
        )


class SelectedBank(BaseModel):
    """A selected bank and the accounts processed for it."""

    model_config = ConfigDict(extra="forbid")

    aspsp_name: str
    aspsp_country: str
    accounts: list[SelectedAccount]


class EnableBankingConfig(BaseModel):
    """Durable Enable Banking selections."""

    model_config = ConfigDict(extra="forbid")

    banks: list[SelectedBank] = Field(default_factory=list)


class PocketSmithMapping(BaseModel):
    """A PocketSmith destination or a persistent decision not to synchronize."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["mapped", "skipped"]
    transaction_account_id: int | None = None
    transaction_account_name: str | None = None

    @model_validator(mode="after")
    def validate_destination(self) -> "PocketSmithMapping":
        """Require destination details only for mapped accounts."""
        if self.status == "mapped" and self.transaction_account_id is None:
            raise ValueError("mapped PocketSmith accounts require an account ID")
        if self.status == "skipped" and (
            self.transaction_account_id is not None
            or self.transaction_account_name is not None
        ):
            raise ValueError("skipped PocketSmith accounts cannot have a destination")
        return self


class PocketSmithConfig(BaseModel):
    """PocketSmith decisions keyed by normalized source IBAN."""

    model_config = ConfigDict(extra="forbid")

    mappings: dict[str, PocketSmithMapping] = Field(default_factory=dict)


class CliConfig(BaseModel):
    """Versioned persistent configuration for one Enable Banking APP_ID."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    enable_banking: EnableBankingConfig = Field(default_factory=EnableBankingConfig)
    pocketsmith: PocketSmithConfig = Field(default_factory=PocketSmithConfig)


def load_cli_config(path: Path) -> CliConfig:
    """Load configuration, returning defaults only when the file is absent."""
    if not path.exists():
        return CliConfig()
    try:
        return CliConfig.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Could not read CLI configuration {path}: {exc}") from exc


def save_cli_config(config: CliConfig, path: Path) -> None:
    """Atomically write configuration with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        path.parent.chmod(0o700)
    content = json.dumps(
        config.model_dump(mode="json"), indent=2, ensure_ascii=False
    )
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(content)
            temporary_file.write("\n")
        if sys.platform != "win32":
            temporary_path.chmod(0o600)
        temporary_path.replace(path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
