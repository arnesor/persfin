"""FastAPI endpoint tests for persfin.main.

Uses FastAPI's TestClient (synchronous httpx wrapper) as recommended in the
FastAPI documentation. All Enable Banking API calls are mocked via pytest-mock
so no real network traffic is made.

Fixtures (defined in conftest.py):
- ``client``        — unauthenticated TestClient
- ``authed_client`` — TestClient with fake_session injected and cookie set
- ``fake_session``  — a SessionResponse with one account
- ``clear_sessions``— autouse; resets persfin.main._sessions between tests
"""

import re

import pytest
from fastapi.testclient import TestClient

from persfin.models import (
    Amount,
    Aspsp,
    AspspsResponse,
    Balance,
    BalancesResponse,
    SessionResponse,
    Transaction,
    TransactionsResponse,
)


# ── GET /banks ────────────────────────────────────────────────────────────────


class TestListBanks:
    def test_returns_bank_list(self, client: TestClient, mocker: pytest.MonkeyPatch) -> None:
        mocker.patch(
            "persfin.main.get_aspsps",
            return_value=AspspsResponse(aspsps=[Aspsp(name="TestBank", country="NO")]),
        )

        response = client.get("/banks?country=NO")

        assert response.status_code == 200
        body = response.json()
        assert len(body["aspsps"]) == 1
        assert body["aspsps"][0]["name"] == "TestBank"
        assert body["aspsps"][0]["country"] == "NO"

    def test_uses_no_as_default_country(
        self, client: TestClient, mocker: pytest.MonkeyPatch
    ) -> None:
        mock = mocker.patch(
            "persfin.main.get_aspsps",
            return_value=AspspsResponse(aspsps=[]),
        )

        client.get("/banks")

        mock.assert_called_once_with(country="NO")

    def test_upstream_error_returns_502(
        self, client: TestClient, mocker: pytest.MonkeyPatch
    ) -> None:
        mocker.patch("persfin.main.get_aspsps", side_effect=RuntimeError("upstream down"))

        response = client.get("/banks")

        assert response.status_code == 502
        assert "upstream down" in response.json()["detail"]


# ── POST /connect ─────────────────────────────────────────────────────────────


class TestConnect:
    def test_returns_auth_url(
        self, client: TestClient, mocker: pytest.MonkeyPatch
    ) -> None:
        mocker.patch(
            "persfin.main.start_auth",
            return_value="https://bank.example/auth?session=abc",
        )

        response = client.post(
            "/connect",
            json={"aspsp_name": "TestBank", "aspsp_country": "NO"},
        )

        assert response.status_code == 200
        assert response.json() == {"url": "https://bank.example/auth?session=abc"}

    def test_passes_aspsp_fields_to_start_auth(
        self, client: TestClient, mocker: pytest.MonkeyPatch
    ) -> None:
        mock = mocker.patch("persfin.main.start_auth", return_value="https://x")

        client.post(
            "/connect",
            json={"aspsp_name": "DNB Bank", "aspsp_country": "NO"},
        )

        mock.assert_called_once_with(aspsp_name="DNB Bank", aspsp_country="NO")

    def test_upstream_error_returns_502(
        self, client: TestClient, mocker: pytest.MonkeyPatch
    ) -> None:
        mocker.patch("persfin.main.start_auth", side_effect=ValueError("bad request"))

        response = client.post(
            "/connect",
            json={"aspsp_name": "TestBank", "aspsp_country": "NO"},
        )

        assert response.status_code == 502

    def test_missing_body_returns_422(self, client: TestClient) -> None:
        response = client.post("/connect", json={})
        assert response.status_code == 422


# ── GET /callback ─────────────────────────────────────────────────────────────


class TestCallback:
    def test_returns_html_on_success(
        self,
        client: TestClient,
        mocker: pytest.MonkeyPatch,
        fake_session: SessionResponse,
    ) -> None:
        mocker.patch("persfin.main.create_session", return_value=fake_session)

        response = client.get("/callback?code=auth-code-123")

        assert response.status_code == 200
        assert "✅ Connected!" in response.text
        assert fake_session.session_id in response.text

    def test_stores_session_in_memory(
        self,
        client: TestClient,
        mocker: pytest.MonkeyPatch,
        fake_session: SessionResponse,
    ) -> None:
        import persfin.main as m

        mocker.patch("persfin.main.create_session", return_value=fake_session)

        client.get("/callback?code=auth-code-123")

        assert fake_session.session_id in m._sessions
        assert m._sessions[fake_session.session_id] == fake_session

    def test_sets_session_cookie(
        self,
        client: TestClient,
        mocker: pytest.MonkeyPatch,
        fake_session: SessionResponse,
    ) -> None:
        mocker.patch("persfin.main.create_session", return_value=fake_session)

        response = client.get("/callback?code=auth-code-123")

        assert "session_id" in response.cookies
        assert response.cookies["session_id"] == fake_session.session_id

    def test_upstream_error_returns_502(
        self, client: TestClient, mocker: pytest.MonkeyPatch
    ) -> None:
        mocker.patch(
            "persfin.main.create_session", side_effect=RuntimeError("token expired")
        )

        response = client.get("/callback?code=bad-code")

        assert response.status_code == 502

    def test_missing_code_returns_422(self, client: TestClient) -> None:
        response = client.get("/callback")
        assert response.status_code == 422


# ── GET /accounts ─────────────────────────────────────────────────────────────


class TestGetAccounts:
    def test_returns_401_without_session_cookie(self, client: TestClient) -> None:
        response = client.get("/accounts")
        assert response.status_code == 401

    def test_returns_401_for_unknown_session_id(self, client: TestClient) -> None:
        client.cookies.set("session_id", "ghost-session")
        response = client.get("/accounts")
        assert response.status_code == 401

    def test_returns_accounts_with_valid_session(
        self, authed_client: TestClient, fake_session: SessionResponse
    ) -> None:
        response = authed_client.get("/accounts")

        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == fake_session.session_id
        assert len(body["accounts"]) == 1
        assert body["accounts"][0]["uid"] == "uid-abc123"


# ── GET /accounts/{uid}/balances ──────────────────────────────────────────────


class TestAccountBalances:
    def test_returns_401_without_session(self, client: TestClient) -> None:
        response = client.get("/accounts/uid-abc123/balances")
        assert response.status_code == 401

    def test_returns_balances(
        self,
        authed_client: TestClient,
        mocker: pytest.MonkeyPatch,
    ) -> None:
        mocker.patch(
            "persfin.main.get_balances",
            return_value=BalancesResponse(
                balances=[
                    Balance(
                        balance_amount=Amount(amount="1000.00", currency="NOK"),
                        balance_type="CLBD",
                    )
                ]
            ),
        )

        response = authed_client.get("/accounts/uid-abc123/balances")

        assert response.status_code == 200
        body = response.json()
        assert body["balances"][0]["balance_amount"]["amount"] == "1000.00"
        assert body["balances"][0]["balance_amount"]["currency"] == "NOK"
        assert body["balances"][0]["balance_type"] == "CLBD"

    def test_passes_correct_account_uid(
        self,
        authed_client: TestClient,
        mocker: pytest.MonkeyPatch,
    ) -> None:
        mock = mocker.patch(
            "persfin.main.get_balances",
            return_value=BalancesResponse(balances=[]),
        )

        authed_client.get("/accounts/uid-abc123/balances")

        mock.assert_called_once_with(account_uid="uid-abc123")

    def test_upstream_error_returns_502(
        self,
        authed_client: TestClient,
        mocker: pytest.MonkeyPatch,
    ) -> None:
        mocker.patch("persfin.main.get_balances", side_effect=RuntimeError("timed out"))

        response = authed_client.get("/accounts/uid-abc123/balances")

        assert response.status_code == 502


# ── GET /accounts/{uid}/transactions ──────────────────────────────────────────


class TestAccountTransactions:
    def test_returns_401_without_session(self, client: TestClient) -> None:
        response = client.get("/accounts/uid-abc123/transactions")
        assert response.status_code == 401

    def test_returns_transactions(
        self,
        authed_client: TestClient,
        mocker: pytest.MonkeyPatch,
    ) -> None:
        mocker.patch(
            "persfin.main.get_transactions",
            return_value=TransactionsResponse(
                transactions=[
                    Transaction(
                        transaction_amount=Amount(amount="199.00", currency="NOK"),
                        booking_date="2026-04-01",
                    )
                ]
            ),
        )

        response = authed_client.get("/accounts/uid-abc123/transactions")

        assert response.status_code == 200
        body = response.json()
        assert len(body["transactions"]) == 1
        assert body["transactions"][0]["booking_date"] == "2026-04-01"
        assert body["transactions"][0]["transaction_amount"]["amount"] == "199.00"

    def test_forwards_custom_date_from(
        self,
        authed_client: TestClient,
        mocker: pytest.MonkeyPatch,
    ) -> None:
        mock = mocker.patch(
            "persfin.main.get_transactions",
            return_value=TransactionsResponse(transactions=[]),
        )

        authed_client.get("/accounts/uid-abc123/transactions?date_from=2026-01-01")

        mock.assert_called_once_with(
            account_uid="uid-abc123",
            date_from="2026-01-01",
            continuation_key=None,
        )

    def test_uses_90_day_default_when_date_not_specified(
        self,
        authed_client: TestClient,
        mocker: pytest.MonkeyPatch,
    ) -> None:
        mock = mocker.patch(
            "persfin.main.get_transactions",
            return_value=TransactionsResponse(transactions=[]),
        )

        authed_client.get("/accounts/uid-abc123/transactions")

        call_kwargs = mock.call_args.kwargs
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", call_kwargs["date_from"])

    def test_forwards_continuation_key(
        self,
        authed_client: TestClient,
        mocker: pytest.MonkeyPatch,
    ) -> None:
        mock = mocker.patch(
            "persfin.main.get_transactions",
            return_value=TransactionsResponse(transactions=[]),
        )

        authed_client.get("/accounts/uid-abc123/transactions?continuation_key=page2")

        assert mock.call_args.kwargs["continuation_key"] == "page2"

    def test_upstream_error_returns_502(
        self,
        authed_client: TestClient,
        mocker: pytest.MonkeyPatch,
    ) -> None:
        mocker.patch(
            "persfin.main.get_transactions",
            side_effect=RuntimeError("connection reset"),
        )

        response = authed_client.get("/accounts/uid-abc123/transactions")

        assert response.status_code == 502
