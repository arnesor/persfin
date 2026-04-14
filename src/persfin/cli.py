"""Interactive CLI for persfin.

Usage:
    uv run persfin-cli

Flow:
    1. Fetches the list of Norwegian banks from Enable Banking.
    2. Prompts the user to pick one.
    3. Opens the OAuth login URL in the browser.
    4. Starts a local FastAPI server to receive the /callback redirect.
    5. Prints account balances and recent transactions after login.
"""

import threading
import time
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import uvicorn

from persfin.enablebanking import get_aspsps, get_balances, get_transactions, start_auth
from persfin.main import app
from persfin.models import CachedSession, SessionResponse

# ── Constants ────────────────────────────────────────────────────────────────

_CACHE_DIR = Path.home() / ".persfin"
_CACHE_FILE = _CACHE_DIR / "session_cache.json"
_SESSION_VALIDITY_DAYS = 90

# ── Helpers ───────────────────────────────────────────────────────────────────


def _prompt_bank_selection(country: str = "NO") -> tuple[str, str]:
    """Fetch ASPSPs for *country*, list them, and return the chosen (name, country)."""
    print(f"\nFetching available banks for country '{country}'…")
    response = get_aspsps(country=country)
    banks = response.aspsps

    if not banks:
        raise SystemExit(f"No banks found for country '{country}'.")

    print(f"\nFound {len(banks)} bank(s):\n")
    for i, bank in enumerate(banks, start=1):
        print(f"  {i:>3}.  {bank.name}")

    while True:
        raw = input(f"\nSelect a bank [1-{len(banks)}]: ").strip()
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(banks):
                selected = banks[choice - 1]
                print(f"\n-> You selected: {selected.name} ({selected.country})")
                return selected.name, selected.country
        print(f"  Please enter a number between 1 and {len(banks)}. Use 1 as default.")


def _start_server_thread() -> None:
    """Run uvicorn in a daemon thread so it stops when the process exits."""
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


def _wait_for_session(timeout: int = 120) -> None:
    """Block until main.py stores a session (user completed login) or timeout."""
    import persfin.main as _persfin_main

    deadline = time.monotonic() + timeout
    print("\nWaiting for you to complete the bank login in your browser…", flush=True)
    while time.monotonic() < deadline:
        if _persfin_main._sessions:
            return
        time.sleep(0.5)
    raise SystemExit("Timed out waiting for bank callback. Please try again.")


def _print_session_summary() -> None:
    """Print account balances and recent transactions for all accounts."""
    import persfin.main as _persfin_main

    if not _persfin_main._sessions:
        print("No session available.")
        return
    session = next(iter(_persfin_main._sessions.values()))

    date_from = (datetime.now(UTC) - timedelta(days=90)).date().isoformat()
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
                print(f"   {'Date':<12} {'Amount':>12} {'Currency':<6}  Description")
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
    """Fetch all transactions for every account in the active session and write one CSV per account."""
    import persfin.main as _persfin_main

    if not _persfin_main._sessions:
        print("No session available — cannot export transactions.")
        return
    session = next(iter(_persfin_main._sessions.values()))

    if output_dir is None:
        output_dir = Path.cwd()
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    date_from = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()

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

        df = pl.DataFrame(rows, infer_schema_length=len(rows) or 1)
        df = df.filter(pl.col("status") != "PDNG")

        safe_name = account.display_name.replace("/", "_").replace("\\", "_")
        csv_path = output_dir / f"{safe_name}.csv"
        df.write_csv(csv_path)
        print(
            f"  Wrote {len(rows)} transaction(s) for account {account.display_name} → {csv_path}"
        )


# ── Session cache ─────────────────────────────────────────────────────────────


def _load_cached_session() -> SessionResponse | None:
    """Load a SessionResponse from the on-disk cache if it exists and has not expired."""
    if not _CACHE_FILE.exists():
        return None
    try:
        cached = CachedSession.model_validate_json(
            _CACHE_FILE.read_text(encoding="utf-8")
        )
        if cached.is_valid():
            print(f"Using cached session (valid until {cached.valid_until.date()}).")
            return cached.to_session_response()
        print("Cached session has expired — re-authenticating.")
    except Exception as exc:
        print(f"Could not read session cache ({exc}) — re-authenticating.")
    return None


def _save_session_cache(session: SessionResponse) -> None:
    """Persist the session to disk so that future runs skip authentication."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    valid_until = datetime.now(UTC) + timedelta(days=_SESSION_VALIDITY_DAYS)
    cached = CachedSession(
        session_id=session.session_id,
        accounts=session.accounts,
        valid_until=valid_until,
    )
    _CACHE_FILE.write_text(cached.model_dump_json(indent=2), encoding="utf-8")
    print(f"Session cached until {valid_until.date()} → {_CACHE_FILE}")


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Run the CLI."""
    import persfin.main as _persfin_main

    # 1. Try to reuse a valid cached session (skips BankID login)
    cached_session = _load_cached_session()
    if cached_session is not None:
        _persfin_main._sessions[cached_session.session_id] = cached_session
    else:
        # 2. No valid cache — run the full OAuth / BankID flow
        aspsp_name, aspsp_country = _prompt_bank_selection(country="NO")

        # 3. Start local HTTPS server so /callback works
        _start_server_thread()

        # 4. Get the OAuth URL and open it in the browser
        auth_url = start_auth(aspsp_name=aspsp_name, aspsp_country=aspsp_country)
        print("\nOpening browser for bank login...")
        print(f"  URL: {auth_url}")
        print()
        if aspsp_name == "Mock ASPSP":
            print("NOTE: Mock ASPSP requires you to be logged into enablebanking.com.")
            print("      1. Make sure you are signed in at https://enablebanking.com")
            print(
                "      2. If you see 'No Account', click 'Create Account' to set up test data"
            )
            print(
                "      3. After creating an account, proceed through the consent flow"
            )
            print()
        print("Waiting up to 10 minutes for you to complete the login...")
        webbrowser.open(auth_url)

        # 5. Wait until the bank redirects back and the session is stored
        _wait_for_session(timeout=600)
        print("\nLogin successful!")

        # 6. Persist the new session so future runs skip authentication
        new_session = next(iter(_persfin_main._sessions.values()))
        _save_session_cache(new_session)

    # 7. Print a summary
    _print_session_summary()

    # 8. Export all transactions to CSV files
    print("\nExporting transactions to CSV...")
    _export_transactions_to_csv()
    print("Done.")


if __name__ == "__main__":
    main()
