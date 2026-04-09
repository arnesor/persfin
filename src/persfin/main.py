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

from fastapi import FastAPI, HTTPException, Query
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

# In-memory store for the active session (single-user local app)
_session: SessionResponse | None = None


# ── Banks ────────────────────────────────────────────────────────────────────


@app.get("/banks", response_model=AspspsResponse, tags=["banks"])
def list_banks(country: str = Query(default="NO", description="ISO 3166 two-letter country code")) -> AspspsResponse:
    """Return the list of supported banks / ASPSPs for a given country."""
    try:
        return enablebanking.get_aspsps(country=country)
    except Exception as exc:
        logger.error("Failed to fetch ASPSPs: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Auth flow ────────────────────────────────────────────────────────────────


@app.post("/connect", tags=["auth"])
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


@app.get("/callback", tags=["auth"])
def callback(code: str = Query(..., description="Authorisation code returned by the bank")) -> HTMLResponse:
    """
    OAuth redirect target. The bank redirects here after the user logs in.
    Exchanges the `code` for a session and stores it in memory.
    """
    global _session
    try:
        _session = enablebanking.create_session(code=code)
        logger.info("Session created: %s (%d accounts)", _session.session_id, len(_session.accounts))
        account_list = "".join(f"<li><code>{a.uid}</code></li>" for a in _session.accounts)
        html = f"""
        <html><body>
        <h2>✅ Connected!</h2>
        <p>Session ID: <code>{_session.session_id}</code></p>
        <p>Accounts:</p><ul>{account_list}</ul>
        <p><a href="/accounts">View accounts JSON</a></p>
        </body></html>
        """
        return HTMLResponse(content=html)
    except Exception as exc:
        logger.error("Callback failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Account data ─────────────────────────────────────────────────────────────


def _require_session() -> SessionResponse:
    if _session is None:
        raise HTTPException(
            status_code=401,
            detail="No active session. Visit /connect first to authenticate with your bank.",
        )
    return _session


@app.get("/accounts", response_model=SessionResponse, tags=["accounts"])
def get_accounts() -> SessionResponse:
    """Return the accounts from the active session."""
    return _require_session()


@app.get("/accounts/{account_uid}/balances", response_model=BalancesResponse, tags=["accounts"])
def account_balances(account_uid: str) -> BalancesResponse:
    """Return balances for the given account UID."""
    _require_session()
    try:
        return enablebanking.get_balances(account_uid=account_uid)
    except Exception as exc:
        logger.error("Failed to fetch balances for %s: %s", account_uid, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/accounts/{account_uid}/transactions", response_model=TransactionsResponse, tags=["accounts"])
def account_transactions(
    account_uid: str,
    date_from: str | None = Query(
        default=None,
        description="Fetch transactions from this date (YYYY-MM-DD). Defaults to 90 days ago.",
    ),
    continuation_key: str | None = Query(default=None, description="Pagination key from a previous response"),
) -> TransactionsResponse:
    """Return transactions for the given account UID."""
    _require_session()
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
