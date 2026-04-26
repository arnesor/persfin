"""Accounts router — GET /accounts, GET /accounts/{uid}/balances|transactions."""

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query

from persfin.core.session_store import StoreDep
from persfin.schemas.schemas import (
    BalancesResponse,
    SessionResponse,
    TransactionsResponse,
)
from persfin.services.enablebanking import get_balances, get_transactions

logger = logging.getLogger(__name__)

router = APIRouter(tags=["accounts"])


def _require_session(
    store: StoreDep,
    session_id: str | None = Cookie(default=None),
) -> SessionResponse:
    """Dependency that resolves the active session from the session cookie.

    Raises:
        HTTPException: 401 if no session cookie is present or the session ID
            is not found in the store.
    """
    if session_id is None or session_id not in store:
        raise HTTPException(
            status_code=401,
            detail="No active session. Visit /connect first to authenticate with your bank.",
        )
    session = store.get(session_id)
    assert session is not None  # guaranteed by the `in` check above
    return session


ActiveSession = Annotated[SessionResponse, Depends(_require_session)]

DateFromQuery = Annotated[
    date | None,
    Query(
        description="Fetch transactions from this date (YYYY-MM-DD). Defaults to 90 days ago."
    ),
]
ContinuationKeyQuery = Annotated[
    str | None, Query(description="Pagination key from a previous response")
]


@router.get(
    "/accounts",
    responses={401: {"description": "No active session"}},
)
def get_accounts(session: ActiveSession) -> SessionResponse:
    """Return the accounts from the active session."""
    return session


@router.get(
    "/accounts/{account_uid}/balances",
    responses={
        401: {"description": "No active session"},
        502: {"description": "Upstream Enable Banking API error"},
    },
)
def account_balances(account_uid: str, _session: ActiveSession) -> BalancesResponse:
    """Return balances for the given account UID."""
    return get_balances(account_uid=account_uid)


@router.get(
    "/accounts/{account_uid}/transactions",
    responses={
        401: {"description": "No active session"},
        502: {"description": "Upstream Enable Banking API error"},
    },
)
def account_transactions(
    account_uid: str,
    _session: ActiveSession,
    date_from: DateFromQuery = None,
    continuation_key: ContinuationKeyQuery = None,
) -> TransactionsResponse:
    """Return transactions for the given account UID."""
    if date_from is None:
        date_from = (datetime.now(UTC) - timedelta(days=90)).date()
    return get_transactions(
        account_uid=account_uid,
        date_from=date_from.isoformat(),
        continuation_key=continuation_key,
    )
