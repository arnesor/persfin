from datetime import datetime
from typing import Any

from pydantic import BaseModel


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

class AccountRef(BaseModel):
    uid: str


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
