"""Shared pytest configuration and fixtures for persfin tests.

Why the os.environ lines are here:
    get_settings() is @lru_cache and is only called when a request actually
    reaches enablebanking code.  All network calls are mocked in the test suite,
    so get_settings() is never truly invoked.  The env vars are a safety net for
    any path that might slip through and try to instantiate Settings().
"""

import os

os.environ.setdefault("APP_ID", "test-app-id")
os.environ.setdefault("PEM_FILE", "dummy.pem")


import pytest
from fastapi.testclient import TestClient

from persfin.core.session_store import SessionStore, get_store
from persfin.main import app
from persfin.schemas.schemas import AccountIdentification, AccountRef, SessionResponse

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
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def authed_client(
    client: TestClient,
    fake_session: SessionResponse,
    fresh_store: SessionStore,
) -> TestClient:
    """A TestClient with a valid session already stored and its cookie set.

    Puts ``fake_session`` into ``fresh_store`` (which is already wired as the
    DI override for ``get_store``) and sets the ``session_id`` cookie on the
    client instance.
    """
    fresh_store.put(fake_session)
    client.cookies.set("session_id", fake_session.session_id)
    return client


# ── State isolation ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_store() -> SessionStore:
    """Provide a clean, isolated SessionStore for each test.

    Overrides the ``get_store`` dependency so every request handled by the
    TestClient uses a fresh store, without touching the module-level ``_store``
    at all.  The override is removed in teardown so it does not bleed into
    subsequent tests.
    """
    store = SessionStore()
    app.dependency_overrides[get_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_store, None)
