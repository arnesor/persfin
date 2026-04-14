"""persfin - personal finance app backed by Enable Banking.

Start the server:
    uv run uvicorn persfin.main:app --reload

Typical flow:
    1. GET  /banks?country=NO            - list available Norwegian banks
    2. POST /connect                     - start OAuth flow, returns bank redirect URL
    3. Browser redirected to bank → user logs in → bank redirects to /callback?code=…
    4. GET  /accounts                    - list accounts from the active session
    5. GET  /accounts/{uid}/balances     - get balances for an account
    6. GET  /accounts/{uid}/transactions - list transactions
"""

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from persfin.enablebanking import (
    create_session,
    get_aspsps,
    get_balances,
    get_transactions,
    start_auth,
)
from persfin.models import (
    AspspsResponse,
    AuthRequest,
    BalancesResponse,
    SessionResponse,
    TransactionsResponse,
)

logger = logging.getLogger(__name__)


# ── Session store ─────────────────────────────────────────────────────────────


class SessionStore:
    """In-memory store for active bank sessions, keyed by session ID.

    Wrapping the dict in a class provides a clean API and makes the store
    injectable via FastAPI's dependency system.  Tests supply a fresh instance
    per test via ``app.dependency_overrides[get_store]``, which means no
    module-level state needs to be cleared between tests.
    """

    def __init__(self) -> None:
        """Initialise an empty session store."""
        self._data: dict[str, SessionResponse] = {}

    def put(self, session: SessionResponse) -> None:
        """Store or replace a session."""
        self._data[session.session_id] = session

    def get(self, session_id: str) -> SessionResponse | None:
        """Return the session for *session_id*, or ``None`` if not found."""
        return self._data.get(session_id)

    def ids(self) -> set[str]:
        """Return the set of all current session IDs."""
        return set(self._data.keys())

    def all(self) -> list[SessionResponse]:
        """Return all stored sessions as a list."""
        return list(self._data.values())

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._data

    def __bool__(self) -> bool:
        return bool(self._data)

    def __len__(self) -> int:
        return len(self._data)


# Module-level store — one instance per process.
# Tests replace this via dependency_overrides rather than mutating it directly.
_store: SessionStore = SessionStore()


def get_store() -> SessionStore:
    """Injectable dependency that returns the active session store.

    Override in tests for isolation::

        test_store = SessionStore()
        app.dependency_overrides[get_store] = lambda: test_store
    """
    return _store


# Annotated aliases used in endpoint signatures.
# Using Annotated keeps Depends() out of argument defaults, which silences
# ruff's B008 rule ("do not perform function call in default arg").
StoreDep = Annotated[SessionStore, Depends(get_store)]


# ── App ───────────────────────────────────────────────────────────────────────


app = FastAPI(
    title="persfin",
    description="Personal finance - Enable Banking integration",
    version="0.1.0",
)


# ── Exception handling ────────────────────────────────────────────────────────


@app.exception_handler(httpx.HTTPError)
async def upstream_error_handler(
    request: Request, exc: httpx.HTTPError
) -> JSONResponse:
    """Convert any httpx error (status error or transport/timeout error) into a 502.

    This replaces the repeated ``try/except → HTTPException(502)`` blocks that
    were in every endpoint, keeping the handlers themselves free of error-handling
    boilerplate.
    """
    logger.error("Enable Banking API error on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=502, content={"detail": str(exc)})


# ── Banks ─────────────────────────────────────────────────────────────────────


CountryQuery = Annotated[str, Query(description="ISO 3166 two-letter country code")]


@app.get(
    "/banks",
    tags=["banks"],
    responses={502: {"description": "Upstream Enable Banking API error"}},
)
def list_banks(country: CountryQuery = "NO") -> AspspsResponse:
    """Return the list of supported banks / ASPSPs for a given country."""
    return get_aspsps(country=country)


# ── Auth flow ─────────────────────────────────────────────────────────────────


@app.post(
    "/connect",
    tags=["auth"],
    responses={502: {"description": "Upstream Enable Banking API error"}},
)
def connect(body: AuthRequest) -> dict[str, str]:
    """Start the authorisation flow for a bank.

    Returns ``{"url": "<bank login URL>"}`` - open that URL in a browser.
    The bank will redirect back to ``/callback?code=…`` when done.
    """
    url = start_auth(aspsp_name=body.aspsp_name, aspsp_country=body.aspsp_country)
    return {"url": url}


AuthCode = Annotated[
    str, Query(..., description="Authorisation code returned by the bank")
]


@app.get(
    "/callback",
    tags=["auth"],
    responses={502: {"description": "Upstream Enable Banking API error"}},
)
def callback(
    code: AuthCode,
    store: StoreDep,
) -> HTMLResponse:
    """OAuth redirect target.

    The bank redirects here after the user logs in. Exchanges the ``code`` for a
    session, stores it in the session store, and sets a ``session_id`` cookie.
    """
    session = create_session(code=code)
    store.put(session)
    logger.info(
        "Session created: %s (%d accounts)",
        session.session_id,
        len(session.accounts),
    )
    account_list = "".join(f"<li><code>{a.uid}</code></li>" for a in session.accounts)
    html = f"""
    <html><body>
    <h2>✅ Connected!</h2>
    <p>Session ID: <code>{session.session_id}</code></p>
    <p>Accounts:</p><ul>{account_list}</ul>
    <p><a href="/accounts">View accounts JSON</a></p>
    </body></html>
    """
    html_response = HTMLResponse(content=html)
    html_response.set_cookie(
        key="session_id", value=session.session_id, httponly=True, samesite="lax"
    )
    return html_response


# ── Account data ──────────────────────────────────────────────────────────────


def _require_session(
    store: StoreDep,
    session_id: str | None = Cookie(default=None),
) -> SessionResponse:
    if session_id is None or session_id not in store:
        raise HTTPException(
            status_code=401,
            detail="No active session. Visit /connect first to authenticate with your bank.",
        )
    session = store.get(session_id)
    assert session is not None  # guaranteed by the `in` check above
    return session


ActiveSession = Annotated[SessionResponse, Depends(_require_session)]


@app.get(
    "/accounts",
    tags=["accounts"],
    responses={401: {"description": "No active session"}},
)
def get_accounts(session: ActiveSession) -> SessionResponse:
    """Return the accounts from the active session."""
    return session


@app.get(
    "/accounts/{account_uid}/balances",
    tags=["accounts"],
    responses={
        401: {"description": "No active session"},
        502: {"description": "Upstream Enable Banking API error"},
    },
)
def account_balances(account_uid: str, _session: ActiveSession) -> BalancesResponse:
    """Return balances for the given account UID."""
    return get_balances(account_uid=account_uid)


DateFromQuery = Annotated[
    date | None,
    Query(
        description="Fetch transactions from this date (YYYY-MM-DD). Defaults to 90 days ago."
    ),
]
ContinuationKeyQuery = Annotated[
    str | None, Query(description="Pagination key from a previous response")
]


@app.get(
    "/accounts/{account_uid}/transactions",
    tags=["accounts"],
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


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Run the server."""
    import uvicorn

    uvicorn.run("persfin.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
