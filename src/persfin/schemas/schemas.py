"""All Pydantic schemas for persfin — request/response and domain models."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, computed_field

# ── ASPSP ─────────────────────────────────────────────────────────────────────


class Aspsp(BaseModel):
    """A single bank / ASPSP entry."""

    name: str
    country: str
    logo: str | None = None
    maximum_consent_validity: int | None = None  # seconds; None means no known limit


class AspspsResponse(BaseModel):
    """Response wrapper for the list of ASPSPs."""

    aspsps: list[Aspsp]


# ── Auth ──────────────────────────────────────────────────────────────────────


class AuthRequest(BaseModel):
    """Request body for POST /connect."""

    aspsp_name: str
    aspsp_country: str


class AuthStartResponse(BaseModel):
    """Response body for a successful auth start."""

    url: str


class AuthStartResult(BaseModel):
    """Internal result of ``start_auth``.

    Carries the bank redirect URL and the exact consent expiry that was
    sent to Enable Banking, so callers can store it precisely.
    """

    url: str
    valid_until: datetime


# ── Account ───────────────────────────────────────────────────────────────────


class AccountIdentification(BaseModel):
    """Primary account identification (IBAN or other scheme)."""

    iban: str | None = None
    other: Any | None = None


class GenericIdentification(BaseModel):
    """Alternative account identifier provided by the ASPSP."""

    identification: str | None = None
    scheme_name: str | None = None


class ClearingSystemMemberId(BaseModel):
    """Clearing system member identifier."""

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


# ── Session ───────────────────────────────────────────────────────────────────


class SessionResponse(BaseModel):
    """Active bank session returned by the Enable Banking API."""

    session_id: str
    accounts: list[AccountRef]


class BankSession(BaseModel):
    """A cached session for a single bank, persisted to disk."""

    aspsp_name: str
    aspsp_country: str
    session_id: str
    accounts: list[AccountRef]
    valid_until: datetime

    def is_valid(self) -> bool:
        """Return True if this session has not expired yet."""
        return datetime.now(UTC) < self.valid_until

    def to_session_response(self) -> SessionResponse:
        """Convert to a plain SessionResponse for use in the app."""
        return SessionResponse(session_id=self.session_id, accounts=self.accounts)


# ── Balances ──────────────────────────────────────────────────────────────────


class Amount(BaseModel):
    """A monetary amount with a currency code."""

    amount: str
    currency: str


class Balance(BaseModel):
    """A single balance entry for an account."""

    name: str | None = None
    balance_amount: Amount
    balance_type: str | None = None
    last_change_date_time: datetime | None = None
    reference_date: str | None = None
    last_committed_transaction: str | None = None


class BalancesResponse(BaseModel):
    """Response wrapper for account balances."""

    balances: list[Balance]


# ── Transactions ──────────────────────────────────────────────────────────────


class Transaction(BaseModel):
    """A single bank transaction."""

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
    """Response wrapper for account transactions, with optional pagination key."""

    transactions: list[Transaction]
    continuation_key: str | None = None
