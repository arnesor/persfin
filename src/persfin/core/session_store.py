"""In-memory session store and FastAPI dependency for persfin."""

import logging
from typing import Annotated

from fastapi import Depends

from persfin.schemas.schemas import SessionResponse

logger = logging.getLogger(__name__)


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
        """Return True if *session_id* is in the store."""
        return session_id in self._data

    def __bool__(self) -> bool:
        """Return True if the store is non-empty."""
        return bool(self._data)

    def __len__(self) -> int:
        """Return the number of sessions in the store."""
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


# Annotated alias used in endpoint signatures.
# Using Annotated keeps Depends() out of argument defaults, which silences
# ruff's B008 rule ("do not perform function call in default arg").
StoreDep = Annotated[SessionStore, Depends(get_store)]
