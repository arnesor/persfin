"""Enable Banking API client with JWT authentication."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt

from persfin.config import settings
from persfin.models import (
    AspspsResponse,
    BalancesResponse,
    SessionResponse,
    TransactionsResponse,
)


def _make_jwt() -> str:
    """Create a signed JWT for Enable Banking API authentication."""
    private_key = settings.pem_file.read_bytes()
    iat = int(datetime.now(UTC).timestamp())
    payload = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": iat,
        "exp": iat + 3600,
    }
    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": settings.app_id},
    )


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_jwt()}"}


def get_aspsps(country: str = "NO") -> AspspsResponse:
    """Fetch the list of available ASPSPs for a given country."""
    with httpx.Client() as client:
        response = client.get(
            f"{settings.api_origin}/aspsps",
            params={"country": country},
            headers=_auth_headers(),
        )
        response.raise_for_status()
        return AspspsResponse.model_validate(response.json())


def start_auth(aspsp_name: str, aspsp_country: str) -> str:
    """Start the authorisation flow for a bank and return the redirect URL."""
    body = {
        "access": {"valid_until": (datetime.now(UTC) + timedelta(days=10)).isoformat()},
        "aspsp": {"name": aspsp_name, "country": aspsp_country},
        "state": str(uuid.uuid4()),
        "redirect_url": settings.redirect_url,
        "psu_type": "personal",
    }
    with httpx.Client() as client:
        response = client.post(
            f"{settings.api_origin}/auth",
            json=body,
            headers=_auth_headers(),
        )
        response.raise_for_status()
        return response.json()["url"]


def create_session(code: str) -> SessionResponse:
    """Exchange the authorisation code for a session."""
    with httpx.Client() as client:
        response = client.post(
            f"{settings.api_origin}/sessions",
            json={"code": code},
            headers=_auth_headers(),
        )
        response.raise_for_status()
        return SessionResponse.model_validate(response.json())


def get_balances(account_uid: str) -> BalancesResponse:
    """Return balances for the given account UID."""
    with httpx.Client() as client:
        response = client.get(
            f"{settings.api_origin}/accounts/{account_uid}/balances",
            headers=_auth_headers(),
        )
        response.raise_for_status()
        return BalancesResponse.model_validate(response.json())


def get_transactions(
    account_uid: str,
    date_from: str | None = None,
    continuation_key: str | None = None,
) -> TransactionsResponse:
    """Return transactions for the given account UID.

    `date_from` should be an ISO 8601 date string (YYYY-MM-DD).
    If `continuation_key` is provided it will be forwarded to page through results.
    """
    params: dict[str, str] = {}
    if date_from:
        params["date_from"] = date_from
    if continuation_key:
        params["continuation_key"] = continuation_key

    with httpx.Client() as client:
        response = client.get(
            f"{settings.api_origin}/accounts/{account_uid}/transactions",
            params=params,
            headers=_auth_headers(),
        )
        response.raise_for_status()
        return TransactionsResponse.model_validate(response.json())
