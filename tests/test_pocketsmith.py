"""Tests for the PocketSmith client and synchronization."""

import json
from datetime import date
from typing import Any

import httpx
import pytest

from persfin.schemas.schemas import Amount, Transaction
from persfin.services.pocketsmith import (
    PocketSmithClient,
    sync_transactions,
    transaction_identity,
)


def _transaction(**overrides: Any) -> Transaction:
    values: dict[str, Any] = {
        "transaction_id": "bank-123",
        "booking_date": "2026-09-18",
        "transaction_amount": Amount(amount="12.50", currency="NOK"),
        "credit_debit_indicator": "DBIT",
        "status": "BOOK",
        "remittance_information": ["Coffee shop"],
    }
    values.update(overrides)
    return Transaction(**values)


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(
        base_url="https://api.pocketsmith.test/v2",
        headers={"X-Developer-Key": "test-key"},
        transport=handler,
    )


class TestPocketSmithClient:
    def test_get_account_uses_injected_authenticated_client(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v2/transaction_accounts/42"
            assert request.headers["X-Developer-Key"] == "test-key"
            return httpx.Response(
                200,
                json={"id": 42, "number": "NO11111111111", "currency_code": "NOK"},
            )

        with _client(httpx.MockTransport(handler)) as http_client:
            account = PocketSmithClient(http_client).get_transaction_account(42)

        assert account.number == "NO11111111111"
        assert account.currency_code == "NOK"

    def test_lists_authorized_users_transaction_accounts(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/v2/me":
                return httpx.Response(200, json={"id": 123})
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 42,
                        "name": "Daily account",
                        "number": "NO11111111111",
                        "currency_code": "NOK",
                    }
                ],
            )

        with _client(httpx.MockTransport(handler)) as http_client:
            accounts = PocketSmithClient(http_client).list_transaction_accounts()

        assert [request.url.path for request in requests] == [
            "/v2/me",
            "/v2/users/123/transaction_accounts",
        ]
        assert accounts[0].id == 42
        assert accounts[0].name == "Daily account"

    def test_lists_all_pages_from_link_header(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json=[{"id": 2, "cheque_number": "b"}])
            return httpx.Response(
                200,
                headers={
                    "Link": '<https://api.pocketsmith.test/v2/transaction_accounts/42/transactions?page=2>; rel="next"'
                },
                json=[{"id": 1, "cheque_number": "a"}],
            )

        with _client(httpx.MockTransport(handler)) as http_client:
            transactions = PocketSmithClient(http_client).list_transactions(
                42, date(2026, 9, 1), date(2026, 9, 19)
            )

        assert [transaction.id for transaction in transactions] == [1, 2]
        assert requests[0].url.params["start_date"] == "2026-09-01"
        assert requests[0].url.params["end_date"] == "2026-09-19"
        assert requests[0].url.params["per_page"] == "1000"

    def test_rejects_cross_origin_pagination_link(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={
                    "Link": '<https://attacker.test/transactions?page=2>; rel="next"'
                },
                json=[],
            )

        with _client(httpx.MockTransport(handler)) as http_client:
            with pytest.raises(ValueError, match="changed origin"):
                PocketSmithClient(http_client).list_transactions(42)


class TestSyncTransactions:
    def test_maps_and_creates_booked_transaction(self) -> None:
        posted_payloads: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/42"):
                return httpx.Response(200, json={"id": 42, "currency_code": "NOK"})
            if request.method == "GET":
                return httpx.Response(200, json=[])
            payload = json.loads(request.content)
            posted_payloads.append(payload)
            return httpx.Response(201, json={"id": 99, **payload})

        transaction = _transaction(additional_information="Card purchase")
        with _client(httpx.MockTransport(handler)) as http_client:
            result = sync_transactions(
                PocketSmithClient(http_client),
                account_id=42,
                account_uid="bank-account-uid",
                transactions=[transaction],
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 19),
            )

        assert result.created == 1
        assert posted_payloads == [
            {
                "payee": "Coffee shop",
                "amount": -12.5,
                "date": "2026-09-18",
                "cheque_number": "persfin:bank-123",
                "note": "Card purchase",
            }
        ]

    def test_skips_duplicate_pending_invalid_and_wrong_currency(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/42"):
                return httpx.Response(200, json={"id": 42, "currency_code": "NOK"})
            if request.method == "GET":
                return httpx.Response(
                    200, json=[{"id": 1, "cheque_number": "persfin:duplicate"}]
                )
            raise AssertionError("No transactions should be created")

        transactions = [
            _transaction(transaction_id="duplicate"),
            _transaction(transaction_id="pending", status="PDNG"),
            _transaction(transaction_id="unknown", status=None),
            _transaction(
                transaction_id="currency",
                transaction_amount=Amount(amount="1.00", currency="EUR"),
            ),
        ]
        with _client(httpx.MockTransport(handler)) as http_client:
            result = sync_transactions(
                PocketSmithClient(http_client),
                account_id=42,
                account_uid="uid",
                transactions=transactions,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 19),
            )

        assert result.duplicates == 1
        assert result.pending == 1
        assert result.invalid == 2
        assert result.created == 0

    def test_credit_uses_debtor_as_payee(self) -> None:
        payloads: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/42"):
                return httpx.Response(200, json={"id": 42, "currency_code": "NOK"})
            if request.method == "GET":
                return httpx.Response(200, json=[])
            payload = json.loads(request.content)
            payloads.append(payload)
            return httpx.Response(201, json={"id": 1})

        transaction = _transaction(
            remittance_information=None,
            creditor_name=None,
            debtor_name="Employer",
            credit_debit_indicator="CRDT",
            transaction_amount=Amount(amount="100.00", currency="NOK"),
        )
        with _client(httpx.MockTransport(handler)) as http_client:
            sync_transactions(
                PocketSmithClient(http_client),
                42,
                "uid",
                [transaction],
                date(2026, 9, 1),
                date(2026, 9, 19),
            )

        assert payloads[0]["payee"] == "Employer"
        assert payloads[0]["amount"] == 100.0

    def test_partial_failure_can_be_retried_without_recreating_success(self) -> None:
        remote_transactions: list[dict[str, Any]] = []
        fail_second = True

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal fail_second
            if request.url.path.endswith("/42"):
                return httpx.Response(200, json={"id": 42, "currency_code": "NOK"})
            if request.method == "GET":
                return httpx.Response(200, json=remote_transactions)
            payload = json.loads(request.content)
            if payload["cheque_number"] == "persfin:second" and fail_second:
                fail_second = False
                return httpx.Response(503, json={"error": "try later"})
            created = {"id": len(remote_transactions) + 1, **payload}
            remote_transactions.append(created)
            return httpx.Response(201, json=created)

        transactions = [
            _transaction(transaction_id="first"),
            _transaction(transaction_id="second"),
        ]
        with _client(httpx.MockTransport(handler)) as http_client:
            client = PocketSmithClient(http_client)
            with pytest.raises(httpx.HTTPStatusError):
                sync_transactions(
                    client,
                    42,
                    "uid",
                    transactions,
                    date(2026, 9, 1),
                    date(2026, 9, 19),
                )
            result = sync_transactions(
                client,
                42,
                "uid",
                transactions,
                date(2026, 9, 1),
                date(2026, 9, 19),
            )

        assert result.duplicates == 1
        assert result.created == 1
        assert [item["cheque_number"] for item in remote_transactions] == [
            "persfin:first",
            "persfin:second",
        ]

    def test_identity_falls_back_to_deterministic_hash(self) -> None:
        transaction = _transaction(transaction_id=None, entry_reference=None)

        first = transaction_identity(transaction, "uid")
        second = transaction_identity(transaction, "uid")

        assert first == second
        assert first.startswith("persfin:sha256:")

    def test_fallback_identity_canonicalizes_amount_and_payee_spacing(self) -> None:
        first = _transaction(
            transaction_id=None,
            entry_reference=None,
            transaction_amount=Amount(amount="12.5", currency="nok"),
            remittance_information=["Coffee  shop"],
        )
        second = _transaction(
            transaction_id=None,
            entry_reference=None,
            transaction_amount=Amount(amount="12.50", currency="NOK"),
            remittance_information=["Coffee shop"],
        )

        assert transaction_identity(first, "uid") == transaction_identity(second, "uid")
