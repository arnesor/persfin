from datetime import datetime
from typing import Any

from pydantic import BaseModel, computed_field


# ── ASPSP ────────────────────────────────────────────────────────────────────

class Aspsp(BaseModel):
    name: str
    country: str
    logo: str | None = None


class AspspsResponse(BaseModel):
    aspsps: list[Aspsp]


# ── Auth ─────────────────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    aspsp_name: str
    aspsp_country: str


class AuthStartResponse(BaseModel):
    url: str


# ── Session ──────────────────────────────────────────────────────────────────

class AccountIdentification(BaseModel):
    """Primary account identification (IBAN or other scheme)."""
    iban: str | None = None
    other: Any | None = None


class GenericIdentification(BaseModel):
    """Alternative account identifier provided by the ASPSP."""
    identification: str | None = None
    scheme_name: str | None = None


class ClearingSystemMemberId(BaseModel):
    clearing_system_id: str | None = None
    member_id: Any | None = None


class AccountServicer(BaseModel):
    """Financial institution that services the account."""
    bic_fi: str | None = None
    clearing_system_member_id: ClearingSystemMemberId | None = None
    name: str | None = None


class AccountRef(BaseModel):
    """Full account resource as returned by the Enable Banking /sessions endpoint."""
    uid: str
    account_id: AccountIdentification | None = None
    all_account_ids: list[GenericIdentification] | None = None
    account_servicer: AccountServicer | None = None
    name: str | None = None
    details: str | None = None
    usage: str | None = None
    cash_account_type: str | None = None
    product: str | None = None
    currency: str | None = None
    psu_status: str | None = None
    credit_limit: Any | None = None
    legal_age: bool | None = None
    postal_address: Any | None = None
    identification_hash: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_name(self) -> str:
        """Human-readable label: IBAN if available, otherwise the uid."""
        if self.account_id and self.account_id.iban:
            return self.account_id.iban
        return self.uid


class SessionResponse(BaseModel):
    session_id: str
    accounts: list[AccountRef]


# ── Balances ─────────────────────────────────────────────────────────────────

class Amount(BaseModel):
    amount: str
    currency: str


class Balance(BaseModel):
    name: str | None = None
    balance_amount: Amount
    balance_type: str | None = None
    last_change_date_time: datetime | None = None
    reference_date: str | None = None
    last_committed_transaction: str | None = None


class BalancesResponse(BaseModel):
    balances: list[Balance]


# ── Transactions ─────────────────────────────────────────────────────────────

class Transaction(BaseModel):
    transaction_id: str | None = None
    entry_reference: str | None = None
    booking_date: str | None = None
    value_date: str | None = None
    transaction_amount: Amount
    creditor_name: str | None = None
    debtor_name: str | None = None
    remittance_information: list[str] | None = None
    additional_information: str | None = None
    merchant_category_code: str | None = None
    balance_after_transaction: Amount | None = None
    credit_debit_indicator: str | None = None
    status: str | None = None
    transaction_details: Any | None = None


class TransactionsResponse(BaseModel):
    transactions: list[Transaction]
    continuation_key: str | None = None
