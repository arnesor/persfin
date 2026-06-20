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

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from persfin.api.accounts import router as accounts_router
from persfin.api.auth import router as auth_router
from persfin.api.banks import router as banks_router

logger = logging.getLogger(__name__)

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


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(banks_router)
app.include_router(auth_router)
app.include_router(accounts_router)


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Run the server."""
    import uvicorn

    uvicorn.run("persfin.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
