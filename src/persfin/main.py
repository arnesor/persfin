"""
persfin – personal finance app backed by Enable Banking.

Start the server:
    uv run uvicorn persfin.main:app --reload

Typical flow:
    1. GET  /banks?country=NO         – list available Norwegian banks
    2. POST /connect                  – start OAuth flow, returns bank redirect URL
    3. Browser redirected to bank → user logs in → bank redirects to /callback?code=…
    4. GET  /accounts                 – list accounts from the active session
    5. GET  /accounts/{uid}/balances
    6. GET  /accounts/{uid}/transactions
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from persfin import enablebanking
from persfin.models import (
    AspspsResponse,
    AuthRequest,
    BalancesResponse,
    SessionResponse,
    TransactionsResponse,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="persfin",
    description="Personal finance – Enable Banking integration",
    version="0.1.0",
)

# In-memory store for sessions, keyed by session ID (supports multiple concurrent sessions)
_sessions: dict[str, SessionResponse] = {}


# ── Banks ────────────────────────────────────────────────────────────────────


CountryQuery = Annotated[str, Query(description="ISO 3166 two-letter country code")]


@app.get("/banks", tags=["banks"], responses={502: {"description": "Upstream Enable Banking API error"}})
def list_banks(country: CountryQuery = "NO") -> AspspsResponse:
    """Return the list of supported banks / ASPSPs for a given country."""
    try:
        return enablebanking.get_aspsps(country=country)
    except Exception as exc:
        logger.error("Failed to fetch ASPSPs: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Auth flow ────────────────────────────────────────────────────────────────


@app.post("/connect", tags=["auth"], responses={502: {"description": "Upstream Enable Banking API error"}})
def connect(body: AuthRequest) -> dict[str, str]:
    """
    Start the authorisation flow for a bank.

    Returns `{"url": "<bank login URL>"}` – open that URL in a browser.
    The bank will redirect back to `/callback?code=…` when done.
    """
    try:
        url = enablebanking.start_auth(
            aspsp_name=body.aspsp_name,
            aspsp_country=body.aspsp_country,
        )
        return {"url": url}
    except Exception as exc:
        logger.error("Failed to start auth: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


AuthCode = Annotated[str, Query(..., description="Authorisation code returned by the bank")]


@app.get("/callback", tags=["auth"], responses={502: {"description": "Upstream Enable Banking API error"}})
def callback(code: AuthCode, response: Response) -> HTMLResponse:
    """
    OAuth redirect target. The bank redirects here after the user logs in.
    Exchanges the `code` for a session, stores it keyed by session ID,
    and sets a `session_id` cookie in the browser.
    """
    try:
        session = enablebanking.create_session(code=code)
        _sessions[session.session_id] = session
        logger.info("Session created: %s (%d accounts)", session.session_id, len(session.accounts))
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
        html_response.set_cookie(key="session_id", value=session.session_id, httponly=True, samesite="lax")
        return html_response
    except Exception as exc:
        logger.error("Callback failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Account data ─────────────────────────────────────────────────────────────


def _require_session(session_id: str | None = Cookie(default=None)) -> SessionResponse:
    if session_id is None or session_id not in _sessions:
        raise HTTPException(
            status_code=401,
            detail="No active session. Visit /connect first to authenticate with your bank.",
        )
    return _sessions[session_id]


ActiveSession = Annotated[SessionResponse, Depends(_require_session)]


@app.get("/accounts", tags=["accounts"], responses={401: {"description": "No active session"}})
def get_accounts(session: ActiveSession) -> SessionResponse:
    """Return the accounts from the active session."""
    return session


@app.get("/accounts/{account_uid}/balances", tags=["accounts"], responses={401: {"description": "No active session"}, 502: {"description": "Upstream Enable Banking API error"}})
def account_balances(account_uid: str, _session: ActiveSession) -> BalancesResponse:
    """Return balances for the given account UID."""
    try:
        return enablebanking.get_balances(account_uid=account_uid)
    except Exception as exc:
        logger.error("Failed to fetch balances for %s: %s", account_uid, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


DateFromQuery = Annotated[str | None, Query(description="Fetch transactions from this date (YYYY-MM-DD). Defaults to 90 days ago.")]
ContinuationKeyQuery = Annotated[str | None, Query(description="Pagination key from a previous response")]


@app.get("/accounts/{account_uid}/transactions", tags=["accounts"], responses={401: {"description": "No active session"}, 502: {"description": "Upstream Enable Banking API error"}})
def account_transactions(
    account_uid: str,
    _session: ActiveSession,
    date_from: DateFromQuery = None,
    continuation_key: ContinuationKeyQuery = None,
) -> TransactionsResponse:
    """Return transactions for the given account UID."""
    if date_from is None:
        date_from = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
    try:
        return enablebanking.get_transactions(
            account_uid=account_uid,
            date_from=date_from,
            continuation_key=continuation_key,
        )
    except Exception as exc:
        logger.error("Failed to fetch transactions for %s: %s", account_uid, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    import uvicorn

    uvicorn.run("persfin.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
