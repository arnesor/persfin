"""Auth router — POST /connect, GET /callback."""

import logging
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from persfin.core.session_store import StoreDep
from persfin.schemas.schemas import AuthRequest
from persfin.services.enablebanking import create_session, start_auth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

AuthCode = Annotated[
    str, Query(..., description="Authorisation code returned by the bank")
]


@router.post(
    "/connect",
    responses={502: {"description": "Upstream Enable Banking API error"}},
)
def connect(body: AuthRequest) -> dict[str, str]:
    """Start the authorisation flow for a bank.

    Returns ``{"url": "<bank login URL>"}`` — open that URL in a browser.
    The bank will redirect back to ``/callback?code=…`` when done.
    """
    url = start_auth(aspsp_name=body.aspsp_name, aspsp_country=body.aspsp_country).url
    return {"url": url}


@router.get(
    "/callback",
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
