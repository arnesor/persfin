"""Enable Banking API client with JWT authentication."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt

from persfin.core.config import get_settings
from persfin.schemas.schemas import (
    AspspsResponse,
    AuthStartResult,
    BalancesResponse,
    SessionResponse,
    TransactionsResponse,
)


def _make_jwt() -> str:
    """Create a signed JWT for Enable Banking API authentication."""
    s = get_settings()
    private_key = s.pem_file.read_bytes()
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
        headers={"kid": s.app_id},
    )


def _auth_headers() -> dict[str, str]:
    """Return Authorization headers with a freshly signed JWT."""
    return {"Authorization": f"Bearer {_make_jwt()}"}


def get_aspsps(country: str = "NO") -> AspspsResponse:
    """Fetch the list of available ASPSPs for a given country."""
    with httpx.Client() as client:
        response = client.get(
            f"{get_settings().api_origin}/aspsps",
            params={"country": country},
            headers=_auth_headers(),
        )
        response.raise_for_status()
        return AspspsResponse.model_validate(response.json())


def start_auth(
    aspsp_name: str,
    aspsp_country: str,
    maximum_consent_validity: int | None = None,
) -> AuthStartResult:
    """Start the authorisation flow for a bank.

    Computes and sends the consent ``valid_until`` capped to the bank's
    ``maximum_consent_validity`` (if known). Returns the redirect URL together
    with the exact ``valid_until`` datetime that was sent, so callers can store
    it precisely rather than recomputing it independently.

    Args:
        aspsp_name: ASPSP name as returned by ``GET /aspsps``.
        aspsp_country: ISO 3166 two-letter country code.
        maximum_consent_validity: Maximum consent duration in seconds as
            reported by the ASPSP, or ``None`` to default to 90 days.

    Returns:
        An :class:`AuthStartResult` with the bank redirect URL and the
        exact ``valid_until`` datetime used in the consent request.
    """
    s = get_settings()
    if maximum_consent_validity is not None:
        max_delta = timedelta(seconds=maximum_consent_validity)
        default_delta = timedelta(days=90)
        delta = min(max_delta, default_delta)
    else:
        delta = timedelta(days=90)
    valid_until = datetime.now(UTC) + delta
    body = {
        "access": {"valid_until": valid_until.isoformat()},
        "aspsp": {"name": aspsp_name, "country": aspsp_country},
        "state": str(uuid.uuid4()),
        "redirect_url": s.redirect_url,
        "psu_type": "personal",
    }
    with httpx.Client() as client:
        response = client.post(
            f"{s.api_origin}/auth",
            json=body,
            headers=_auth_headers(),
        )
        response.raise_for_status()
        return AuthStartResult(url=response.json()["url"], valid_until=valid_until)


def create_session(code: str) -> SessionResponse:
    """Exchange the authorisation code for a session."""
    with httpx.Client() as client:
        response = client.post(
            f"{get_settings().api_origin}/sessions",
            json={"code": code},
            headers=_auth_headers(),
        )
        response.raise_for_status()
        return SessionResponse.model_validate(response.json())


def get_balances(account_uid: str) -> BalancesResponse:
    """Return balances for the given account UID."""
    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            f"{get_settings().api_origin}/accounts/{account_uid}/balances",
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

    Args:
        account_uid: The unique account identifier.
        date_from: ISO 8601 date string (YYYY-MM-DD) for the start of the range.
        continuation_key: Pagination key from a previous response.

    Returns:
        A ``TransactionsResponse`` with transactions and an optional continuation key.
    """
    params: dict[str, str] = {}
    if date_from:
        params["date_from"] = date_from
    if continuation_key:
        params["continuation_key"] = continuation_key

    with httpx.Client(timeout=60.0) as client:
        response = client.get(
            f"{get_settings().api_origin}/accounts/{account_uid}/transactions",
            params=params,
            headers=_auth_headers(),
        )
        response.raise_for_status()
        return TransactionsResponse.model_validate(response.json())
