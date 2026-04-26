"""Interactive CLI for persfin.

Usage:
    uv run persfin-cli

Flow (first run):
    1. Fetches the list of Norwegian banks from Enable Banking.
    2. Prompts the user to pick one or more banks.
    3. For each bank: opens the OAuth login URL in the browser and waits for
       the /callback redirect via a local FastAPI server.
    4. Saves all sessions to ~/.persfin/session_cache_<app_id>.json.
    5. Prints account balances and exports transactions to CSV.

Subsequent runs:
    - Loads valid cached sessions and skips authentication entirely.
    - Re-authenticates only sessions that have expired.
"""

import asyncio
import json
import sys
import threading
import time
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import polars as pl
import uvicorn

from persfin.core.session_store import get_store
from persfin.main import app
from persfin.schemas.schemas import BankSession, SessionResponse
from persfin.services.enablebanking import (
    get_aspsps,
    get_balances,
    get_transactions,
    start_auth,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_CACHE_DIR = Path.home() / ".persfin"
# Project root is three levels above this file: src/persfin/cli.py -> project root
_DATA_DIR = Path(__file__).parent.parent.parent / "data"


def _make_cache_file() -> Path:
    """Return the session cache path for the current APP_ID.

    Each APP_ID gets its own file, e.g.:
        ~/.persfin/session_cache_77521a6a-5a1e-44a7-9044-c84a214b6153.json

    This lets you switch between prod and sandbox APP_IDs in .env without
    losing the other environment's cached sessions.
    """
    from persfin.core.config import get_settings

    app_id = get_settings().app_id
    return _CACHE_DIR / f"session_cache_{app_id}.json"


# Resolved once at import time so tests can override it with monkeypatch.setattr.
_CACHE_FILE: Path = _make_cache_file()


def _cache_key(aspsp_name: str, aspsp_country: str) -> str:
    """Return a stable dict key for a bank, e.g. ``'DNB Bank|NO'``."""
    return f"{aspsp_name}|{aspsp_country}"


class BankToAuth(NamedTuple):
    """A bank that needs (re-)authentication."""

    aspsp_name: str
    aspsp_country: str
    maximum_consent_validity: int | None  # seconds, or None to use the default


# ── Helpers ───────────────────────────────────────────────────────────────────


def _prompt_bank_multi_selection(
    country: str = "NO",
) -> list[BankToAuth]:
    """Fetch ASPSPs for *country* and let the user pick one or more banks.

    Returns a list of :class:`BankToAuth` in the order selected.
    ``maximum_consent_validity`` is in seconds, or ``None`` if the ASPSP
    does not advertise a limit.
    """
    print(f"\nFetching available banks for country '{country}'…")
    response = get_aspsps(country=country)
    banks = response.aspsps

    if not banks:
        raise SystemExit(f"No banks found for country '{country}'.")

    print(f"\nFound {len(banks)} bank(s):\n")
    for i, bank in enumerate(banks, start=1):
        print(f"  {i:>3}.  {bank.name}")

    print("\nEnter the numbers of the banks you want to connect to,")
    print("separated by spaces or commas.  Example:  1 3  or  1,3")

    while True:
        raw = input("\nSelect banks: ").strip()
        parts = raw.replace(",", " ").split()
        if not parts:
            print("  Please enter at least one number.")
            continue
        try:
            indices = [int(p) for p in parts]
        except ValueError:
            print("  Invalid input — please enter numbers only.")
            continue
        if all(1 <= idx <= len(banks) for idx in indices):
            seen: set[int] = set()
            selected: list[BankToAuth] = []
            for idx in indices:
                if idx not in seen:
                    seen.add(idx)
                    bank = banks[idx - 1]
                    selected.append(
                        BankToAuth(
                            aspsp_name=bank.name,
                            aspsp_country=bank.country,
                            maximum_consent_validity=bank.maximum_consent_validity,
                        )
                    )
            print("\n→ Selected bank(s):")
            for b in selected:
                print(f"    - {b.aspsp_name} ({b.aspsp_country})")
            return selected
        print(f"  Please enter numbers between 1 and {len(banks)}.")


def _start_server_thread() -> None:
    """Run uvicorn in a daemon thread so it stops when the process exits."""
    # WinError 10054: ProactorEventLoop (Windows default) doesn't handle remote
    # connection resets gracefully. Switching to SelectorEventLoop silences the
    # spurious "Exception in callback _ProactorBasePipeTransport" tracebacks.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    certdir = Path(__file__).parent.parent.parent / "firefly" / "certs"
    certfile = certdir / "localhost+2.pem"
    keyfile = certdir / "localhost+2-key.pem"
    assert certfile.exists() and keyfile.exists()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
        ssl_certfile=certfile,
        ssl_keyfile=keyfile,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait until the server is ready before continuing
    for _ in range(20):
        time.sleep(0.25)
        if server.started:
            break


def _wait_for_new_session(known_ids: set[str], timeout: int = 600) -> str:
    """Block until a session ID not in *known_ids* appears in the store.

    Returns the new session ID once the bank callback has been processed.
    Uses ``get_store()`` so it reads from the same store that the running
    FastAPI server writes to.
    """
    store = get_store()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new = store.ids() - known_ids
        if new:
            return next(iter(new))
        time.sleep(0.5)
    raise SystemExit("Timed out waiting for bank callback. Please try again.")


def _export_transactions_to_csv(
    sessions: list[SessionResponse],
    days: int = 90,
    output_dir: Path | None = None,
) -> None:
    """Fetch transactions for every account, print a preview, and write one CSV per account.

    For each account:
    - Prints the IBAN / account identifier and balances.
    - Fetches all transactions (paging through continuation keys).
    - Prints a preview of up to 20 rows.
    - Writes the full set to a CSV file.

    Only one fetch per account is made, avoiding bank-side rate limits that
    can trigger a 429 when the same endpoint is called twice in quick succession.
    """
    if not sessions:
        print("No session available — cannot export transactions.")
        return

    if output_dir is None:
        output_dir = Path.cwd()
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    date_from = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()

    for session in sessions:
        print(f"\n{'=' * 60}")
        print(f"  Session ID : {session.session_id}")
        print(f"  Accounts   : {len(session.accounts)}")
        print(f"{'=' * 60}")

        for account in session.accounts:
            uid = account.uid
            print(f"\n-- Account: {account.display_name} (uid: {uid}) --")

            # Balances
            try:
                bal_resp = get_balances(account_uid=uid)
                for b in bal_resp.balances:
                    label = b.balance_type or b.name or "balance"
                    print(
                        f"   {label:<30} {b.balance_amount.amount} {b.balance_amount.currency}"
                    )
            except Exception as exc:
                print(f"   (Could not fetch balances: {exc})")

            # Fetch all transactions (single call, paged)
            rows: list[dict] = []
            continuation_key: str | None = None
            fetch_error: str | None = None

            while True:
                try:
                    resp = get_transactions(
                        account_uid=uid,
                        date_from=date_from,
                        continuation_key=continuation_key,
                    )
                except Exception as exc:
                    fetch_error = str(exc)
                    break

                rows.extend(
                    {
                        "booking_date": t.booking_date,
                        "amount": t.transaction_amount.amount,
                        "currency": t.transaction_amount.currency,
                        "credit_debit_indicator": t.credit_debit_indicator,
                        "status": t.status,
                        "remittance_information": (
                            "|".join(t.remittance_information)
                            if t.remittance_information
                            else None
                        ),
                    }
                    for t in resp.transactions
                )
                continuation_key = resp.continuation_key
                if not continuation_key:
                    break

            # Print transaction preview (up to 20 rows)
            print(f"\n   Transactions since {date_from} ({len(rows)} total):")
            if fetch_error:
                print(f"   (Could not fetch transactions: {fetch_error})")
            elif rows:
                print(f"   {'Date':<12} {'Amount':>14} {'Currency':<6}  Description")
                print(f"   {'-' * 12} {'-' * 14} {'-' * 6}  {'-' * 30}")
                for row in rows[:20]:
                    print(
                        f"   {(row['booking_date'] or '???'):<12}"
                        f" {row['amount']:>14}"
                        f" {row['currency']:<6}"
                        f"  {(row['remittance_information'] or '')[:50]}"
                    )
                if len(rows) > 20:
                    print(f"   … and {len(rows) - 20} more (all written to CSV).")
            else:
                print("   (no transactions found in this period)")

            # Write CSV
            if not rows:
                print(
                    f"  (No transactions fetched for {account.display_name} — skipping CSV)"
                )
                continue

            df = pl.DataFrame(rows, infer_schema_length=len(rows))
            df = df.filter(pl.col("status") != "PDNG")

            safe_name = account.display_name.replace("/", "_").replace("\\", "_")
            csv_path = output_dir / f"{safe_name}.csv"
            df.write_csv(csv_path)
            print(f"   → Wrote {len(rows)} row(s) to {csv_path}")

    print(f"\n{'=' * 60}\n")


# ── Session cache ─────────────────────────────────────────────────────────────


def _load_session_cache() -> dict[str, BankSession]:
    """Load all BankSessions from the APP_ID-specific cache file (valid *and* expired).

    Returns an empty dict if the file is missing or unreadable.
    Keys are ``"<aspsp_name>|<aspsp_country>"``.
    """
    cache_file = _CACHE_FILE
    if not cache_file.exists():
        return {}
    try:
        raw: dict = json.loads(cache_file.read_text(encoding="utf-8"))
        return {k: BankSession.model_validate(v) for k, v in raw.items()}
    except Exception as exc:
        print(f"Could not read session cache ({exc}) — starting fresh.")
        return {}


def _save_session_cache(sessions: dict[str, BankSession]) -> None:
    """Persist all BankSessions to the APP_ID-specific cache file on disk.

    On Linux/macOS the cache directory is created with permissions 0o700 and the
    file with 0o600 so that only the owning user can read the session tokens.
    Windows does not support POSIX permission bits, so the chmod calls are skipped.
    """
    cache_file = _CACHE_FILE
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {k: json.loads(v.model_dump_json()) for k, v in sessions.items()}
    cache_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if sys.platform != "win32":
        _CACHE_DIR.chmod(0o700)  # rwx------  (owner only)
        cache_file.chmod(0o600)  # rw-------  (owner only)
    print(f"Session cache updated → {cache_file}")


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Run the CLI."""
    store = get_store()

    # 1. Load all cached bank sessions (both valid and expired)
    all_cached = _load_session_cache()
    valid = {k: v for k, v in all_cached.items() if v.is_valid()}
    expired = {k: v for k, v in all_cached.items() if not v.is_valid()}

    # 2. Inject valid sessions into the in-memory store
    for bs in valid.values():
        store.put(bs.to_session_response())

    # 3. Determine which banks need (re-)authentication
    if not all_cached:
        # First run — ask the user which banks to connect to
        print("\nNo cached sessions found. Let's connect your bank(s).")
        banks_to_auth = _prompt_bank_multi_selection(country="NO")
    elif expired:
        # Some sessions have expired — re-authenticate only those
        print(
            f"\n{len(expired)} cached session(s) have expired and need re-authentication:"
        )
        for bs in expired.values():
            print(f"  - {bs.aspsp_name} ({bs.aspsp_country})")
        banks_to_auth = [
            BankToAuth(bs.aspsp_name, bs.aspsp_country, None)
            for bs in expired.values()
        ]
    else:
        # All sessions are valid — nothing to do
        count = len(valid)
        print(f"\nAll {count} cached session(s) are valid — skipping authentication.")
        banks_to_auth = []

    # 4. Authenticate each bank that needs it (one shared server, sequential logins)
    if banks_to_auth:
        _start_server_thread()

        for bank in banks_to_auth:
            known_ids = store.ids()
            auth_result = start_auth(
                aspsp_name=bank.aspsp_name,
                aspsp_country=bank.aspsp_country,
                maximum_consent_validity=bank.maximum_consent_validity,
            )

            print(f"\nOpening browser for {bank.aspsp_name}…")
            print(f"  URL: {auth_result.url}")
            if bank.aspsp_name == "Mock ASPSP":
                print(
                    "  (Mock ASPSP: make sure you are signed in at enablebanking.com)"
                )
            print("  Waiting up to 10 minutes for you to complete the login…")
            webbrowser.open(auth_result.url)

            new_session_id = _wait_for_new_session(known_ids, timeout=600)
            new_session = store.get(new_session_id)
            assert new_session is not None

            key = _cache_key(bank.aspsp_name, bank.aspsp_country)
            all_cached[key] = BankSession(
                aspsp_name=bank.aspsp_name,
                aspsp_country=bank.aspsp_country,
                session_id=new_session.session_id,
                accounts=new_session.accounts,
                valid_until=auth_result.valid_until,
            )
            print(
                f"  ✓ Authenticated with {bank.aspsp_name}"
                f" (session valid until {auth_result.valid_until.date()})"
            )

        _save_session_cache(all_cached)

    # 5. Fetch, display, and export transactions
    _export_transactions_to_csv(store.all(), output_dir=_DATA_DIR)


if __name__ == "__main__":
    main()
