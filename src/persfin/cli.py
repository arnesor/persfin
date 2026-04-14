"""Interactive CLI for persfin.

Usage:
    uv run persfin-cli

Flow (first run):
    1. Fetches the list of Norwegian banks from Enable Banking.
    2. Prompts the user to pick one or more banks.
    3. For each bank: opens the OAuth login URL in the browser and waits for
       the /callback redirect via a local FastAPI server.
    4. Saves all sessions to ~/.persfin/session_cache.json (valid for 90 days).
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

import polars as pl
import uvicorn

from persfin.enablebanking import get_aspsps, get_balances, get_transactions, start_auth
from persfin.main import app
from persfin.models import BankSession

# ── Constants ─────────────────────────────────────────────────────────────────

_CACHE_DIR = Path.home() / ".persfin"
_CACHE_FILE = _CACHE_DIR / "session_cache.json"
_SESSION_VALIDITY_DAYS = 90


def _cache_key(aspsp_name: str, aspsp_country: str) -> str:
    """Return a stable dict key for a bank, e.g. ``'DNB Bank|NO'``."""
    return f"{aspsp_name}|{aspsp_country}"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _prompt_bank_multi_selection(country: str = "NO") -> list[tuple[str, str]]:
    """Fetch ASPSPs for *country* and let the user pick one or more banks.

    Returns a list of ``(name, country)`` tuples in the order selected.
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
            selected: list[tuple[str, str]] = []
            for idx in indices:
                if idx not in seen:
                    seen.add(idx)
                    bank = banks[idx - 1]
                    selected.append((bank.name, bank.country))
            print("\n→ Selected bank(s):")
            for name, cty in selected:
                print(f"    - {name} ({cty})")
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
    """Block until a session ID not in *known_ids* appears in the in-memory store.

    Returns the new session ID once the bank callback has been processed.
    """
    import persfin.main as _persfin_main

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new = set(_persfin_main._sessions.keys()) - known_ids
        if new:
            return next(iter(new))
        time.sleep(0.5)
    raise SystemExit("Timed out waiting for bank callback. Please try again.")


def _print_session_summary() -> None:
    """Print account balances and recent transactions for every active session."""
    import persfin.main as _persfin_main

    if not _persfin_main._sessions:
        print("No session available.")
        return

    date_from = (datetime.now(UTC) - timedelta(days=90)).date().isoformat()

    for session in _persfin_main._sessions.values():
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

            # Transactions
            try:
                txn_resp = get_transactions(account_uid=uid, date_from=date_from)
                txns = txn_resp.transactions
                print(f"\n   Transactions since {date_from} ({len(txns)} total):")
                if txns:
                    print(
                        f"   {'Date':<12} {'Amount':>12} {'Currency':<6}  Description"
                    )
                    print(f"   {'-' * 12} {'-' * 12} {'-' * 6}  {'-' * 30}")
                    for t in txns[:20]:
                        date = t.booking_date or t.value_date or "???"
                        amt = t.transaction_amount.amount
                        ccy = t.transaction_amount.currency
                        desc = (
                            (
                                t.remittance_information[0]
                                if t.remittance_information
                                else None
                            )
                            or t.creditor_name
                            or t.debtor_name
                            or t.additional_information
                            or ""
                        )
                        print(f"   {date:<12} {amt:>12} {ccy:<6}  {desc[:50]}")
                    if len(txns) > 20:
                        print(
                            f"   … and {len(txns) - 20} more. Use the API for the full list."
                        )
                else:
                    print("   (no transactions found in this period)")
            except Exception as exc:
                print(f"   (Could not fetch transactions: {exc})")

    print(f"\n{'=' * 60}\n")


def _export_transactions_to_csv(days: int = 90, output_dir: Path | None = None) -> None:
    """Fetch all transactions for every account across all sessions and write one CSV per account."""
    import persfin.main as _persfin_main

    if not _persfin_main._sessions:
        print("No session available — cannot export transactions.")
        return

    if output_dir is None:
        output_dir = Path.cwd()
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    date_from = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()

    for session in _persfin_main._sessions.values():
        for account in session.accounts:
            uid = account.uid
            rows: list[dict] = []

            # Page through all transactions using continuation_key
            continuation_key: str | None = None
            while True:
                try:
                    resp = get_transactions(
                        account_uid=uid,
                        date_from=date_from,
                        continuation_key=continuation_key,
                    )
                except Exception as exc:
                    print(f"  (Could not fetch transactions for {uid}: {exc})")
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
            print(
                f"  Wrote {len(rows)} transaction(s) for account {account.display_name} → {csv_path}"
            )


# ── Session cache ─────────────────────────────────────────────────────────────


def _load_session_cache() -> dict[str, BankSession]:
    """Load all BankSessions from the cache file (valid *and* expired).

    Returns an empty dict if the file is missing or unreadable.
    Keys are ``"<aspsp_name>|<aspsp_country>"``.
    """
    if not _CACHE_FILE.exists():
        return {}
    try:
        raw: dict = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        return {k: BankSession.model_validate(v) for k, v in raw.items()}
    except Exception as exc:
        print(f"Could not read session cache ({exc}) — starting fresh.")
        return {}


def _save_session_cache(sessions: dict[str, BankSession]) -> None:
    """Persist all BankSessions to disk.

    On Linux/macOS the cache directory is created with permissions 0o700 and the
    file with 0o600 so that only the owning user can read the session tokens.
    Windows does not support POSIX permission bits, so the chmod calls are skipped.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {k: json.loads(v.model_dump_json()) for k, v in sessions.items()}
    _CACHE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if sys.platform != "win32":
        _CACHE_DIR.chmod(0o700)   # rwx------  (owner only)
        _CACHE_FILE.chmod(0o600)  # rw-------  (owner only)
    print(f"Session cache updated → {_CACHE_FILE}")


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Run the CLI."""
    import persfin.main as _persfin_main

    # 1. Load all cached bank sessions (both valid and expired)
    all_cached = _load_session_cache()
    valid = {k: v for k, v in all_cached.items() if v.is_valid()}
    expired = {k: v for k, v in all_cached.items() if not v.is_valid()}

    # 2. Inject valid sessions into the in-memory store
    for bs in valid.values():
        _persfin_main._sessions[bs.session_id] = bs.to_session_response()

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
        banks_to_auth = [(bs.aspsp_name, bs.aspsp_country) for bs in expired.values()]
    else:
        # All sessions are valid — nothing to do
        count = len(valid)
        print(f"\nAll {count} cached session(s) are valid — skipping authentication.")
        banks_to_auth = []

    # 4. Authenticate each bank that needs it (one shared server, sequential logins)
    if banks_to_auth:
        _start_server_thread()

        for aspsp_name, aspsp_country in banks_to_auth:
            known_ids = set(_persfin_main._sessions.keys())
            auth_url = start_auth(aspsp_name=aspsp_name, aspsp_country=aspsp_country)

            print(f"\nOpening browser for {aspsp_name}…")
            print(f"  URL: {auth_url}")
            if aspsp_name == "Mock ASPSP":
                print(
                    "  (Mock ASPSP: make sure you are signed in at enablebanking.com)"
                )
            print("  Waiting up to 10 minutes for you to complete the login…")
            webbrowser.open(auth_url)

            new_session_id = _wait_for_new_session(known_ids, timeout=600)
            new_session = _persfin_main._sessions[new_session_id]

            valid_until = datetime.now(UTC) + timedelta(days=_SESSION_VALIDITY_DAYS)
            key = _cache_key(aspsp_name, aspsp_country)
            all_cached[key] = BankSession(
                aspsp_name=aspsp_name,
                aspsp_country=aspsp_country,
                session_id=new_session.session_id,
                accounts=new_session.accounts,
                valid_until=valid_until,
            )
            print(
                f"  ✓ Authenticated with {aspsp_name}"
                f" (session valid until {valid_until.date()})"
            )

        _save_session_cache(all_cached)

    # 5. Print a summary across all sessions
    _print_session_summary()

    # 6. Export all transactions to CSV files
    print("\nExporting transactions to CSV…")
    _export_transactions_to_csv()
    print("Done.")


if __name__ == "__main__":
    main()
