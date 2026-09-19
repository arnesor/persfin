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
import csv
import json
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import httpx
import polars as pl
import typer
import uvicorn

from persfin.core.session_store import get_store
from persfin.main import app
from persfin.schemas.schemas import (
    AccountRef,
    BankSession,
    SessionResponse,
    Transaction,
)
from persfin.services.enablebanking import (
    get_aspsps,
    get_balances,
    get_transactions,
    start_auth,
)
from persfin.services.pocketsmith import (
    PocketSmithClient,
    PocketSmithSyncResult,
    PocketSmithTransaction,
    PocketSmithTransactionAccount,
    sync_transactions,
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


@dataclass(frozen=True)
class PocketSmithSyncConfig:
    """PocketSmith dependencies and account mapping for one CLI run."""

    client: PocketSmithClient
    transaction_account_id: int
    source_iban: str
    cutover_date: date | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _prompt_bank_multi_selection(
    country: str = "NO",
) -> list[BankToAuth]:
    """Fetch ASPSPs for *country* and let the user pick one or more banks.

    Returns a list of :class:`BankToAuth` in the order selected.
    ``maximum_consent_validity`` is in seconds, or ``None`` if the ASPSP
    does not advertise a limit.
    """
    print(f"\nFetching available banks for country '{country}'...")
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
            print("\n-> Selected bank(s):")
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


def _invalidate_session(session_id: str) -> None:
    """Remove a session from the cache file if it becomes invalid (e.g. 401 Unauthorized)."""
    try:
        cache = _load_session_cache()
        keys_to_remove = [k for k, v in cache.items() if v.session_id == session_id]
        if keys_to_remove:
            for k in keys_to_remove:
                del cache[k]
            _save_session_cache(cache)
            print(
                f"   -> Automatically removed invalid session {session_id} from cache."
            )
    except Exception as exc:
        print(f"   (Could not remove invalid session from cache: {exc})")


def _validate_from_date(value: str | None) -> date | None:
    """Validate that the input is a valid YYYY-MM-DD date and is today or earlier."""
    if value is None:
        return None

    try:
        parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as e:
        raise typer.BadParameter("Invalid date format. Must be YYYY-MM-DD.") from e

    if parsed_date > date.today():
        raise typer.BadParameter("Date must be today or earlier.")

    return parsed_date


def _find_source_account(
    sessions: list[SessionResponse], source_iban: str
) -> AccountRef:
    """Find exactly one account whose primary IBAN matches the source IBAN."""
    normalized_iban = _normalize_account_number(source_iban)
    matches = [
        account
        for session in sessions
        for account in session.accounts
        if account.account_id is not None
        and account.account_id.iban is not None
        and _normalize_account_number(account.account_id.iban) == normalized_iban
    ]
    if not matches:
        raise ValueError(
            f"Bank account {source_iban} was not found in active sessions."
        )
    if len(matches) > 1:
        raise ValueError(f"Bank account {source_iban} appears in multiple sessions.")
    return matches[0]


def _pocketsmith_start_date(
    from_date: date | None, cutover_date: date | None, default_start_date: date
) -> date:
    """Return the latest configured start date, falling back to the CLI default."""
    candidates = [
        candidate for candidate in (from_date, cutover_date) if candidate is not None
    ]
    return max(candidates) if candidates else default_start_date


def _select_pocketsmith_account(
    accounts: list[PocketSmithTransactionAccount], source_iban: str
) -> int:
    """Automatically match an IBAN or prompt for a PocketSmith account."""
    normalized_iban = _normalize_account_number(source_iban)
    matches = [
        account
        for account in accounts
        if account.number is not None
        and _normalize_account_number(account.number) == normalized_iban
    ]
    if len(matches) == 1:
        account = matches[0]
        print(
            f"Matched PocketSmith account: {account.name or 'Unnamed account'} "
            f"({account.number}, id: {account.id})"
        )
        return account.id

    if not accounts:
        raise ValueError("No PocketSmith transaction accounts were found.")

    if len(matches) > 1:
        print(f"Multiple PocketSmith accounts match {source_iban}.")
    else:
        print(f"No PocketSmith account number matches {source_iban}.")
    print("Select the PocketSmith destination account:\n")
    for index, account in enumerate(accounts, start=1):
        print(
            f"  {index:>3}. {account.name or 'Unnamed account'} "
            f"(number: {account.number or 'not set'}, id: {account.id})"
        )

    while True:
        raw = input("\nPocketSmith account number: ").strip()
        try:
            selected = int(raw)
        except ValueError:
            print("  Please enter a number from the list.")
            continue
        if 1 <= selected <= len(accounts):
            return accounts[selected - 1].id
        print(f"  Please enter a number between 1 and {len(accounts)}.")


def _normalize_account_number(value: str) -> str:
    return "".join(value.split()).upper()


def _fetch_all_transactions(
    account_uid: str,
    date_from: str,
    on_page: Callable[[list[Transaction]], None] | None = None,
) -> list[Transaction]:
    """Fetch every Enable Banking transaction page for an account."""
    transactions: list[Transaction] = []
    continuation_key: str | None = None
    while True:
        response = get_transactions(
            account_uid=account_uid,
            date_from=date_from,
            continuation_key=continuation_key,
        )
        transactions.extend(response.transactions)
        if on_page is not None:
            on_page(transactions)
        continuation_key = response.continuation_key
        if not continuation_key:
            return transactions


def _write_debug_csv(records: list[dict[str, object]], path: Path) -> None:
    """Atomically write complete API records with JSON-encoded values."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as csv_file:
            temporary_path = Path(csv_file.name)
            if records:
                fieldnames = list(
                    dict.fromkeys(field for record in records for field in record)
                )
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(
                    {
                        field: _debug_csv_value(
                            record[field] if field in record else _MISSING_DEBUG_VALUE
                        )
                        for field in fieldnames
                    }
                    for record in records
                )
        assert temporary_path is not None
        if sys.platform != "win32":
            temporary_path.chmod(0o600)
        temporary_path.replace(path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _debug_csv_value(value: object) -> object:
    if value is _MISSING_DEBUG_VALUE:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


_MISSING_DEBUG_VALUE = object()


def _export_transactions_to_csv(
    sessions: list[SessionResponse],
    from_date: date | None = None,
    output_dir: Path | None = None,
    pocketsmith: PocketSmithSyncConfig | None = None,
    debug: bool = False,
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
        print("No session available - cannot export transactions.")
        return

    if output_dir is None:
        output_dir = Path.cwd()
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    default_start_date = from_date or (datetime.now(UTC) - timedelta(days=90)).date()
    source_account = (
        _find_source_account(sessions, pocketsmith.source_iban)
        if pocketsmith is not None
        else None
    )
    sync_start_date = (
        _pocketsmith_start_date(from_date, pocketsmith.cutover_date, default_start_date)
        if pocketsmith is not None
        else None
    )

    for session in sessions:
        print(f"\n{'=' * 60}")
        print(f"  Session ID : {session.session_id}")
        print(f"  Accounts   : {len(session.accounts)}")
        print(f"{'=' * 60}")

        session_expired = False
        for account in session.accounts:
            uid = account.uid
            safe_name = account.display_name.replace("/", "_").replace("\\", "_")
            account_start_date = (
                sync_start_date
                if source_account is not None and account is source_account
                else default_start_date
            )
            assert account_start_date is not None
            date_from = account_start_date.isoformat()
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
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code == 401
                ):
                    print(
                        f"   (Could not fetch balances: {exc} - session has expired/been revoked on the server)"
                    )
                    _invalidate_session(session.session_id)
                    session_expired = True
                    break
                else:
                    print(f"   (Could not fetch balances: {exc})")

            # Fetch all transactions (single call, paged)
            transactions: list[Transaction] = []
            fetch_error: str | None = None
            enable_banking_debug_path = output_dir / f"{safe_name}_debug.csv"
            debug_snapshot_written = False
            debug_record_count = 0
            if debug:
                try:
                    enable_banking_debug_path.unlink(missing_ok=True)
                except OSError as exc:
                    print(f"   (Could not clear Enable Banking debug CSV: {exc})")

            def write_enable_banking_debug(
                records: list[Transaction],
                debug_path: Path = enable_banking_debug_path,
            ) -> None:
                nonlocal debug_snapshot_written, debug_record_count
                try:
                    _write_debug_csv(
                        [
                            transaction.model_dump(mode="json", exclude_unset=True)
                            for transaction in records
                        ],
                        debug_path,
                    )
                except Exception as exc:
                    print(f"   (Could not write Enable Banking debug CSV: {exc})")
                    return
                debug_snapshot_written = True
                debug_record_count = len(records)

            try:
                transactions = _fetch_all_transactions(
                    uid,
                    date_from,
                    on_page=write_enable_banking_debug if debug else None,
                )
            except Exception as exc:
                fetch_error = str(exc)
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code == 401
                ):
                    fetch_error += " - session has expired/been revoked on the server"
                    _invalidate_session(session.session_id)
                    session_expired = True

            rows = [
                {
                    "booking_date": transaction.booking_date,
                    "amount": transaction.transaction_amount.amount,
                    "currency": transaction.transaction_amount.currency,
                    "credit_debit_indicator": transaction.credit_debit_indicator,
                    "status": transaction.status,
                    "remittance_information": (
                        "|".join(transaction.remittance_information)
                        if transaction.remittance_information
                        else None
                    ),
                }
                for transaction in transactions
            ]

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
                    print(f"   ... and {len(rows) - 20} more (all written to CSV).")
            else:
                print("   (no transactions found in this period)")

            if debug_snapshot_written:
                print(
                    f"   -> Wrote {debug_record_count} full Enable Banking "
                    f"record(s) to {enable_banking_debug_path}"
                )

            if session_expired:
                break

            # Write CSV
            if not rows:
                print(
                    f"  (No transactions fetched for {account.display_name} - skipping CSV)"
                )
                continue

            df = pl.DataFrame(rows, infer_schema_length=len(rows))

            amount = pl.col("amount").cast(pl.Decimal(scale=2), strict=True)

            df = df.filter(pl.col("status") != "PDNG").with_columns(
                pl.when(pl.col("credit_debit_indicator") == "CRDT")
                .then(amount)
                .when(pl.col("credit_debit_indicator") == "DBIT")
                .then(-amount)
                .otherwise(None)
                .alias("amount")
            )

            csv_path = output_dir / f"{safe_name}.csv"
            df.write_csv(csv_path)
            print(f"   -> Wrote {df.height} row(s) to {csv_path}")

            if (
                pocketsmith is not None
                and source_account is not None
                and account is source_account
            ):
                assert sync_start_date is not None
                pocketsmith_debug_path = (
                    output_dir
                    / f"pocketsmith_{pocketsmith.transaction_account_id}_debug.csv"
                )
                pocketsmith_debug_count = 0
                pocketsmith_debug_written = False
                if debug:
                    try:
                        pocketsmith_debug_path.unlink(missing_ok=True)
                    except OSError as exc:
                        print(f"   (Could not clear PocketSmith debug CSV: {exc})")

                def write_pocketsmith_debug(
                    records: list[PocketSmithTransaction],
                    debug_path: Path = pocketsmith_debug_path,
                ) -> None:
                    nonlocal pocketsmith_debug_count, pocketsmith_debug_written
                    try:
                        _write_debug_csv(
                            [
                                transaction.model_dump(mode="json", exclude_unset=True)
                                for transaction in records
                            ],
                            debug_path,
                        )
                    except Exception as exc:
                        print(f"   (Could not write PocketSmith debug CSV: {exc})")
                        return
                    pocketsmith_debug_count = len(records)
                    pocketsmith_debug_written = True

                result = sync_transactions(
                    client=pocketsmith.client,
                    account_id=pocketsmith.transaction_account_id,
                    account_uid=account.uid,
                    transactions=transactions,
                    start_date=sync_start_date,
                    existing_transactions_callback=(
                        write_pocketsmith_debug if debug else None
                    ),
                )
                _print_pocketsmith_result(result)
                if pocketsmith_debug_written:
                    print(
                        f"   -> Wrote {pocketsmith_debug_count} full "
                        f"PocketSmith record(s) to {pocketsmith_debug_path}"
                    )

    print(f"\n{'=' * 60}\n")


def _print_pocketsmith_result(result: PocketSmithSyncResult) -> None:
    print(
        "   -> PocketSmith: "
        f"{result.created} created, "
        f"{result.duplicates} duplicate(s), "
        f"{result.pending} pending, "
        f"{result.invalid} invalid"
    )


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
    data = {k: v.model_dump(mode="json") for k, v in sessions.items()}
    cache_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if sys.platform != "win32":
        _CACHE_DIR.chmod(0o700)  # rwx------  (owner only)
        cache_file.chmod(0o600)  # rw-------  (owner only)
    print(f"Session cache updated -> {cache_file}")


# ── Entry point ───────────────────────────────────────────────────────────────


cli_app = typer.Typer(add_completion=False)


@cli_app.command()
def run_cli(
    from_date: str = typer.Option(
        None,
        "--from-date",
        "-fd",
        help="Only retrieve transactions from this date (YYYY-MM-DD) and later. Must be in the past or today.",
    ),
    pocketsmith: bool = typer.Option(
        False,
        "--pocketsmith",
        help="Upload booked transactions from the configured bank account to PocketSmith.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Write full Enable Banking and PocketSmith transaction CSV files.",
    ),
) -> None:
    """Run the CLI."""
    parsed_date = _validate_from_date(from_date)
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
            BankToAuth(bs.aspsp_name, bs.aspsp_country, None) for bs in expired.values()
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

            print(f"\nOpening browser for {bank.aspsp_name}...")
            print(f"  URL: {auth_result.url}")
            if bank.aspsp_name == "Mock ASPSP":
                print(
                    "  (Mock ASPSP: make sure you are signed in at enablebanking.com)"
                )
            print("  Waiting up to 10 minutes for you to complete the login...")
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

    # 5. Fetch, display, export, and optionally synchronize transactions
    sessions = store.all()
    if not pocketsmith:
        _export_transactions_to_csv(
            sessions, from_date=parsed_date, output_dir=_DATA_DIR, debug=debug
        )
        return

    from persfin.core.config import get_settings

    settings = get_settings()
    developer_key = settings.pocketsmith_developer_key
    transaction_account_id = settings.pocketsmith_transaction_account_id
    cutover_date = settings.pocketsmith_cutover_date
    missing: list[str] = []
    if developer_key is None:
        missing.append("POCKETSMITH_DEVELOPER_KEY")
    if missing:
        raise typer.BadParameter(
            f"Missing PocketSmith configuration: {', '.join(missing)}",
            param_hint="--pocketsmith",
        )
    assert developer_key is not None
    if cutover_date is not None and cutover_date > date.today():
        raise typer.BadParameter(
            "POCKETSMITH_CUTOVER_DATE must be today or earlier.",
            param_hint="--pocketsmith",
        )

    with httpx.Client(
        base_url=settings.pocketsmith_api_origin,
        headers={"X-Developer-Key": developer_key.get_secret_value()},
        timeout=30,
    ) as http_client:
        pocketsmith_client = PocketSmithClient(http_client)
        if transaction_account_id is None:
            transaction_account_id = _select_pocketsmith_account(
                pocketsmith_client.list_transaction_accounts(),
                settings.pocketsmith_source_iban,
            )
        sync_config = PocketSmithSyncConfig(
            client=pocketsmith_client,
            transaction_account_id=transaction_account_id,
            source_iban=settings.pocketsmith_source_iban,
            cutover_date=cutover_date,
        )
        _export_transactions_to_csv(
            sessions,
            from_date=parsed_date,
            output_dir=_DATA_DIR,
            pocketsmith=sync_config,
            debug=debug,
        )


def main() -> None:
    """Entrypoint wrapper for console scripts."""
    cli_app()


if __name__ == "__main__":
    main()
