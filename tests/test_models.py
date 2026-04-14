"""Unit tests for persfin.models."""

from datetime import UTC, datetime, timedelta

from persfin.models import (
    AccountIdentification,
    AccountRef,
    BankSession,
    SessionResponse,
)

# ── AccountRef.display_name ───────────────────────────────────────────────────


class TestAccountRefDisplayName:
    def test_returns_iban_when_present(self) -> None:
        account = AccountRef(
            uid="uid-1",
            account_id=AccountIdentification(iban="NO9386011117947"),
        )
        assert account.display_name == "NO9386011117947"

    def test_falls_back_to_uid_when_no_account_id(self) -> None:
        account = AccountRef(uid="uid-no-iban")
        assert account.display_name == "uid-no-iban"

    def test_falls_back_to_uid_when_iban_is_none(self) -> None:
        account = AccountRef(
            uid="uid-null-iban",
            account_id=AccountIdentification(iban=None),
        )
        assert account.display_name == "uid-null-iban"


# ── BankSession.is_valid ──────────────────────────────────────────────────────


class TestBankSessionIsValid:
    def test_returns_true_when_not_expired(self, fake_account: AccountRef) -> None:
        bs = BankSession(
            aspsp_name="TestBank",
            aspsp_country="NO",
            session_id="s1",
            accounts=[fake_account],
            valid_until=datetime.now(UTC) + timedelta(days=30),
        )
        assert bs.is_valid() is True

    def test_returns_false_when_expired(self, fake_account: AccountRef) -> None:
        bs = BankSession(
            aspsp_name="TestBank",
            aspsp_country="NO",
            session_id="s1",
            accounts=[fake_account],
            valid_until=datetime.now(UTC) - timedelta(seconds=1),
        )
        assert bs.is_valid() is False

    def test_boundary_just_expired(self, fake_account: AccountRef) -> None:
        """A session expiring in the past (even by 1 µs) must be invalid."""
        bs = BankSession(
            aspsp_name="TestBank",
            aspsp_country="NO",
            session_id="s1",
            accounts=[fake_account],
            valid_until=datetime.now(UTC) - timedelta(microseconds=1),
        )
        assert bs.is_valid() is False


# ── BankSession.to_session_response ──────────────────────────────────────────


class TestBankSessionToSessionResponse:
    def test_returns_session_response_instance(self, fake_account: AccountRef) -> None:
        bs = BankSession(
            aspsp_name="TestBank",
            aspsp_country="NO",
            session_id="session-abc",
            accounts=[fake_account],
            valid_until=datetime.now(UTC) + timedelta(days=1),
        )
        result = bs.to_session_response()
        assert isinstance(result, SessionResponse)

    def test_preserves_session_id(self, fake_account: AccountRef) -> None:
        bs = BankSession(
            aspsp_name="TestBank",
            aspsp_country="NO",
            session_id="session-abc",
            accounts=[fake_account],
            valid_until=datetime.now(UTC) + timedelta(days=1),
        )
        assert bs.to_session_response().session_id == "session-abc"

    def test_preserves_accounts(self, fake_account: AccountRef) -> None:
        bs = BankSession(
            aspsp_name="TestBank",
            aspsp_country="NO",
            session_id="s",
            accounts=[fake_account],
            valid_until=datetime.now(UTC) + timedelta(days=1),
        )
        sr = bs.to_session_response()
        assert len(sr.accounts) == 1
        assert sr.accounts[0].uid == fake_account.uid

    def test_does_not_include_aspsp_fields(self, fake_account: AccountRef) -> None:
        """SessionResponse must not expose aspsp_name / aspsp_country."""
        bs = BankSession(
            aspsp_name="SecretBank",
            aspsp_country="NO",
            session_id="s",
            accounts=[fake_account],
            valid_until=datetime.now(UTC) + timedelta(days=1),
        )
        sr = bs.to_session_response()
        assert not hasattr(sr, "aspsp_name")
        assert not hasattr(sr, "aspsp_country")
