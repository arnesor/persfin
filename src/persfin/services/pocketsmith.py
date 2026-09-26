"""PocketSmith API client and transaction synchronization."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from persfin.schemas.schemas import AccountIdentification, Transaction


class PocketSmithTransactionAccount(BaseModel):
    """Destination transaction account returned by PocketSmith."""

    id: int
    name: str | None = None
    number: str | None = None
    currency_code: str


class PocketSmithUser(BaseModel):
    """Authorized PocketSmith user."""

    id: int


class PocketSmithTransaction(BaseModel):
    """PocketSmith transaction retaining all fields returned by the API."""

    model_config = ConfigDict(extra="allow")

    id: int
    memo: str | None = None


@dataclass(frozen=True)
class PocketSmithSyncResult:
    """Counts produced by a PocketSmith synchronization."""

    created: int = 0
    first_created_date: date | None = None
    last_created_date: date | None = None
    duplicates: int = 0
    pending: int = 0
    invalid: int = 0
    existing_transactions: tuple[PocketSmithTransaction, ...] = ()


class PocketSmithClient:
    """PocketSmith API operations using an injected HTTP client."""

    def __init__(self, client: httpx.Client) -> None:
        """Use the caller-owned HTTP client for every API request."""
        self._client = client

    def get_transaction_account(self, account_id: int) -> PocketSmithTransactionAccount:
        """Return a PocketSmith transaction account by ID."""
        response = self._client.get(f"/transaction_accounts/{account_id}")
        response.raise_for_status()
        return PocketSmithTransactionAccount.model_validate(response.json())

    def list_transaction_accounts(self) -> list[PocketSmithTransactionAccount]:
        """Return every transaction account belonging to the authorized user."""
        user_response = self._client.get("/me")
        user_response.raise_for_status()
        user = PocketSmithUser.model_validate(user_response.json())

        accounts_response = self._client.get(f"/users/{user.id}/transaction_accounts")
        accounts_response.raise_for_status()
        return [
            PocketSmithTransactionAccount.model_validate(item)
            for item in accounts_response.json()
        ]

    def list_transactions(
        self,
        account_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        on_page: Callable[[list[PocketSmithTransaction]], None] | None = None,
    ) -> list[PocketSmithTransaction]:
        """Return every accessible transaction, optionally within a date range."""
        url: str | httpx.URL = f"/transaction_accounts/{account_id}/transactions"
        request_params: dict[str, str | int] = {"per_page": 1000}
        if (start_date is None) != (end_date is None):
            raise ValueError("start_date and end_date must be provided together")
        if start_date is not None and end_date is not None:
            request_params["start_date"] = start_date.isoformat()
            request_params["end_date"] = end_date.isoformat()
        params: dict[str, str | int] | None = request_params
        transactions: list[PocketSmithTransaction] = []

        while True:
            response = self._client.get(url, params=params)
            response.raise_for_status()
            transactions.extend(
                PocketSmithTransaction.model_validate(item) for item in response.json()
            )
            if on_page is not None:
                on_page(transactions)
            next_link = response.links.get("next")
            if next_link is None:
                return transactions
            next_url = httpx.URL(next_link["url"])
            if _origin(next_url) != _origin(response.request.url):
                raise ValueError("PocketSmith pagination link changed origin")
            url = next_url
            params = None

    def create_transaction(
        self, account_id: int, payload: dict[str, Any]
    ) -> PocketSmithTransaction:
        """Create one transaction in a PocketSmith transaction account."""
        response = self._client.post(
            f"/transaction_accounts/{account_id}/transactions", json=payload
        )
        response.raise_for_status()
        return PocketSmithTransaction.model_validate(response.json())

def transaction_identity(transaction: Transaction, account_uid: str) -> str:
    """Return a stable PocketSmith memo for a bank transaction."""
    source_id = transaction.transaction_id or transaction.entry_reference
    if source_id:
        return f"persfin:{source_id}"

    raw_identity = "\x1f".join(
        (
            account_uid,
            transaction.booking_date or "",
            format(Decimal(transaction.transaction_amount.amount).normalize(), "f"),
            transaction.transaction_amount.currency.upper(),
            transaction.credit_debit_indicator or "",
            " ".join(_identity_payee(transaction).split()),
        )
    )
    digest = hashlib.sha256(raw_identity.encode()).hexdigest()
    return f"persfin:sha256:{digest}"


def sync_transactions(
    client: PocketSmithClient,
    account_id: int,
    account_uid: str,
    transactions: list[Transaction],
    start_date: date,
    end_date: date | None = None,
    existing_transactions_callback: (
        Callable[[list[PocketSmithTransaction]], None] | None
    ) = None,
) -> PocketSmithSyncResult:
    """Create eligible bank transactions that are not already in PocketSmith."""
    sync_end_date = end_date or date.today()
    destination = client.get_transaction_account(account_id)
    existing = client.list_transactions(
        account_id,
        start_date,
        sync_end_date,
        on_page=existing_transactions_callback,
    )
    identities = {transaction.memo for transaction in existing if transaction.memo}
    fallback_occurrences: dict[str, int] = {}
    created_dates: list[date] = []
    created = duplicates = pending = invalid = 0

    for transaction in transactions:
        if transaction.status == "PDNG":
            pending += 1
            continue
        if transaction.status != "BOOK":
            invalid += 1
            continue

        try:
            transaction_date = date.fromisoformat(transaction.booking_date or "")
            signed_amount = _signed_amount(transaction)
        except (ValueError, InvalidOperation):
            invalid += 1
            continue

        if transaction_date < start_date or transaction_date > sync_end_date:
            continue
        if (
            transaction.transaction_amount.currency.upper()
            != destination.currency_code.upper()
        ):
            invalid += 1
            continue

        identity = transaction_identity(transaction, account_uid)
        if transaction.transaction_id is None and transaction.entry_reference is None:
            occurrence = fallback_occurrences.get(identity, 0) + 1
            fallback_occurrences[identity] = occurrence
            if occurrence > 1:
                identity = f"{identity}:{occurrence}"
        if identity in identities:
            duplicates += 1
            continue

        payload: dict[str, Any] = {
            "payee": transaction_description(transaction),
            "amount": float(signed_amount),
            "date": transaction_date.isoformat(),
            "memo": identity,
        }
        if transaction.additional_information:
            payload["note"] = transaction.additional_information

        client.create_transaction(account_id, payload)
        identities.add(identity)
        created_dates.append(transaction_date)
        created += 1

    return PocketSmithSyncResult(
        created=created,
        first_created_date=min(created_dates) if created_dates else None,
        last_created_date=max(created_dates) if created_dates else None,
        duplicates=duplicates,
        pending=pending,
        invalid=invalid,
        existing_transactions=tuple(existing),
    )


def _signed_amount(transaction: Transaction) -> Decimal:
    amount = abs(Decimal(transaction.transaction_amount.amount))
    if transaction.credit_debit_indicator == "CRDT":
        return amount
    if transaction.credit_debit_indicator == "DBIT":
        return -amount
    raise ValueError("Unknown credit/debit indicator")


def transaction_description(transaction: Transaction) -> str:
    """Combine remittance and directional counterparty details for PocketSmith."""
    parts = [
        value.strip()
        for value in transaction.remittance_information or []
        if value.strip()
    ]
    if transaction.credit_debit_indicator == "DBIT":
        name = transaction.creditor_name
        account = _account_number(transaction.creditor_account)
    elif transaction.credit_debit_indicator == "CRDT":
        name = transaction.debtor_name
        account = _account_number(transaction.debtor_account)
    else:
        name = transaction.creditor_name or transaction.debtor_name
        account = _account_number(
            transaction.creditor_account or transaction.debtor_account
        )
    if name is None and account is None:
        name = transaction.creditor_name or transaction.debtor_name

    existing = _searchable(" ".join(parts))
    name_missing = bool(name and _searchable(name) not in existing)
    account_missing = bool(account and _searchable(account) not in existing)
    if name_missing and account_missing:
        assert name is not None and account is not None
        parts.append(f"{name} ({account})")
    elif name_missing:
        assert name is not None
        parts.append(name)
    elif account_missing:
        assert account is not None
        parts.append(account)

    if parts:
        return " | ".join(parts)
    return transaction.additional_information or "Unknown payee"


def _identity_payee(transaction: Transaction) -> str:
    """Return the legacy payee text used by persisted fallback identities."""
    if transaction.remittance_information:
        return " | ".join(transaction.remittance_information)
    if transaction.credit_debit_indicator == "DBIT" and transaction.creditor_name:
        return transaction.creditor_name
    if transaction.credit_debit_indicator == "CRDT" and transaction.debtor_name:
        return transaction.debtor_name
    return (
        transaction.creditor_name
        or transaction.debtor_name
        or transaction.additional_information
        or "Unknown payee"
    )


def _account_number(account: AccountIdentification | None) -> str | None:
    if account is None:
        return None
    if account.iban:
        return account.iban
    if isinstance(account.other, dict):
        identification = account.other.get("identification")
        return identification if isinstance(identification, str) else None
    identification = getattr(account.other, "identification", None)
    return identification if isinstance(identification, str) else None


def _searchable(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    return url.scheme, url.host, url.port
