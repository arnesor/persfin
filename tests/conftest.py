"""Shared pytest configuration and fixtures for persfin tests.

The os.environ lines must appear before any persfin import so that
pydantic-settings' Settings() instantiation (which happens at module import
time) can satisfy its required fields without a real .env file.
"""

import os

# Provide stub values so Settings() validates on import without a .env file.
# The actual values are never used in tests because all network calls are mocked.
os.environ.setdefault("APP_ID", "test-app-id")
os.environ.setdefault("PEM_FILE", "dummy.pem")

from datetime import UTC, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from persfin.models import AccountIdentification, AccountRef, SessionResponse  # noqa: E402


# ── Reusable model fixtures ───────────────────────────────────────────────────


@pytest.fixture()
def fake_account() -> AccountRef:
    """A minimal AccountRef with a known UID and IBAN."""
    return AccountRef(
        uid="uid-abc123",
        account_id=AccountIdentification(iban="NO1234567890123"),
    )


@pytest.fixture()
def fake_session(fake_account: AccountRef) -> SessionResponse:
    """A SessionResponse containing one fake account."""
    return SessionResponse(session_id="sess-xyz", accounts=[fake_account])


# ── FastAPI test client ───────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    """A synchronous FastAPI TestClient backed by the persfin app."""
    from persfin.main import app  # local import keeps module order safe

    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def authed_client(
    client: TestClient, fake_session: SessionResponse
) -> TestClient:
    """A TestClient with a valid session already injected and its cookie set.

    Injects ``fake_session`` into ``persfin.main._sessions`` and sets the
    ``session_id`` cookie directly on the client instance so no per-request
    ``cookies=`` argument is needed (which Starlette has deprecated).
    """
    import persfin.main as m

    m._sessions[fake_session.session_id] = fake_session
    client.cookies.set("session_id", fake_session.session_id)
    return client


# ── State isolation ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_sessions() -> None:
    """Clear the in-memory session store before and after every test."""
    import persfin.main as m

    m._sessions.clear()
    yield
    m._sessions.clear()
