"""
Interactive CLI for persfin.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn

from persfin.enablebanking import get_aspsps, get_balances, get_transactions, start_auth
from persfin.main import app


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
        raw = input(f"\nSelect a bank [1–{len(banks)}]: ").strip()
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(banks):
                selected = banks[choice - 1]
                print(f"\n-> You selected: {selected.name} ({selected.country})")
                return selected.name, selected.country
        print(f"  Please enter a number between 1 and {len(banks)}. Use 1 as default.")


def _start_server_thread() -> None:
    certdir = Path(__file__).parent.parent.parent / "firefly" / "certs"
    certfile = certdir / "localhost+2.pem"
    keyfile = certdir / "localhost+2-key.pem"
    assert certfile.exists() and keyfile.exists()
    """Run uvicorn in a daemon thread so it stops when the process exits."""
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning",
                            ssl_certfile=certfile,
                            ssl_keyfile=keyfile)
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

    date_from = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
    print(f"\n{'=' * 60}")
    print(f"  Session ID : {session.session_id}")
    print(f"  Accounts   : {len(session.accounts)}")
    print(f"{'=' * 60}")

    for account in session.accounts:
        uid = account.uid
        print(f"\n-- Account: {uid} --")

        # Balances
        try:
            bal_resp = get_balances(account_uid=uid)
            for b in bal_resp.balances:
                label = b.balance_type or b.name or "balance"
                print(
                    f"   {label:<30} {b.balance_amount.amount} {b.balance_amount.currency}")
        except Exception as exc:  # noqa: BLE001
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
                            (t.remittance_information[
                                 0] if t.remittance_information else None)
                            or t.creditor_name
                            or t.debtor_name
                            or t.additional_information
                            or ""
                    )
                    print(f"   {date:<12} {amt:>12} {ccy:<6}  {desc[:50]}")
                if len(txns) > 20:
                    print(
                        f"   … and {len(txns) - 20} more. Use the API for the full list.")
            else:
                print("   (no transactions found in this period)")
        except Exception as exc:  # noqa: BLE001
            print(f"   (Could not fetch transactions: {exc})")

    print(f"\n{'=' * 60}\n")


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    # 1. Let user pick a bank
    aspsp_name, aspsp_country = _prompt_bank_selection(country="NO")

    # 2. Start local server so /callback works
    _start_server_thread()

    # 3. Get the OAuth URL and open it
    auth_url = start_auth(aspsp_name=aspsp_name, aspsp_country=aspsp_country)
    print("\nOpening browser for bank login...")
    print(f"  URL: {auth_url}")
    print()
    if aspsp_name == "Mock ASPSP":
        print("NOTE: Mock ASPSP requires you to be logged into enablebanking.com.")
        print("      1. Make sure you are signed in at https://enablebanking.com")
        print(
            "      2. If you see 'No Account', click 'Create Account' to set up test data")
        print("      3. After creating an account, proceed through the consent flow")
        print()
    print("Waiting up to 10 minutes for you to complete the login...")
    webbrowser.open(auth_url)

    # 4. Wait until the bank redirects back and the session is stored
    _wait_for_session(timeout=600)
    print("\nLogin successful!")

    # 5. Print a summary
    _print_session_summary()


if __name__ == "__main__":
    main()
