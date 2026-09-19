"""PocketSmith API client and transaction synchronization."""

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import BaseModel

from persfin.schemas.schemas import Transaction


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
    """Subset of a PocketSmith transaction needed for deduplication."""

    id: int
    cheque_number: str | None = None


@dataclass(frozen=True)
class PocketSmithSyncResult:
    """Counts produced by a PocketSmith synchronization."""

    created: int = 0
    duplicates: int = 0
    pending: int = 0
    invalid: int = 0


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
    """Return a stable PocketSmith cheque number for a bank transaction."""
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
            " ".join(_payee(transaction).split()),
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
) -> PocketSmithSyncResult:
    """Create eligible bank transactions that are not already in PocketSmith."""
    sync_end_date = end_date or date.today()
    destination = client.get_transaction_account(account_id)
    existing = client.list_transactions(account_id, start_date, sync_end_date)
    identities = {
        transaction.cheque_number
        for transaction in existing
        if transaction.cheque_number is not None
    }
    fallback_occurrences: dict[str, int] = {}
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
            "payee": _payee(transaction),
            "amount": float(signed_amount),
            "date": transaction_date.isoformat(),
            "cheque_number": identity,
        }
        if transaction.additional_information:
            payload["note"] = transaction.additional_information

        client.create_transaction(account_id, payload)
        identities.add(identity)
        created += 1

    return PocketSmithSyncResult(
        created=created,
        duplicates=duplicates,
        pending=pending,
        invalid=invalid,
    )


def _signed_amount(transaction: Transaction) -> Decimal:
    amount = abs(Decimal(transaction.transaction_amount.amount))
    if transaction.credit_debit_indicator == "CRDT":
        return amount
    if transaction.credit_debit_indicator == "DBIT":
        return -amount
    raise ValueError("Unknown credit/debit indicator")


def _payee(transaction: Transaction) -> str:
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


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    return url.scheme, url.host, url.port
