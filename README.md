# persfin

Personal finance app that fetches Norwegian bank transactions via [Enable Banking](https://enablebanking.com).

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- A sandbox (or production) app registered at [enablebanking.com](https://enablebanking.com)
- The private key `.pem` file downloaded when registering the app, placed in the project root

## Setup

```bash
# Install dependencies
uv sync
```

### Configuration

The app ships with sensible defaults for the sandbox.  
Override any setting via a `.env` file in the project root or environment variables:

| Variable         | Default                                    | Description                              |
|------------------|--------------------------------------------|------------------------------------------|
| `APP_ID`         | `1ac8f792-e6bf-4a66-8512-244359e69fcf`     | Enable Banking application ID            |
| `PEM_FILE`       | `1ac8f792-e6bf-4a66-8512-244359e69fcf.pem` | Path to the RSA private key              |
| `REDIRECT_URL`   | `http://localhost:8000/callback`            | OAuth callback URL (must be whitelisted) |
| `API_ORIGIN`     | `https://api.enablebanking.com`             | Enable Banking API base URL              |
| `ASPSP_NAME`     | `Sbanken`                                  | Default bank name                        |
| `ASPSP_COUNTRY`  | `NO`                                       | Default bank country (ISO 3166)          |

Example `.env`:
```dotenv
ASPSP_NAME=Sbanken
ASPSP_COUNTRY=NO
```

## Quick start – interactive CLI

The easiest way to use persfin is the interactive CLI.
It lists all Norwegian banks, lets you pick one, opens the bank login in your
browser, and then prints your balances and recent transactions.

```bash
uv run persfin-cli
```

Sample session:

```
Fetching available banks for country 'NO'…

Found 23 bank(s):

    1.  BRAbank
    2.  DNB Bank
    3.  Eika Alliansen
    …
   23.  Sbanken

Select a bank [1–23]: 23

→ You selected: Sbanken (NO)

Opening browser for bank login…
  https://auth.enablebanking.com/…

Waiting for you to complete the bank login in your browser…

✅ Login successful!

════════════════════════════════════════════════════════════
  Session ID : xxxxxxxx-…
  Accounts   : 2
════════════════════════════════════════════════════════════

── Account: <uid1> ──
   closingBooked                    12345.67 NOK
   …
```

The bank sandbox login credentials are typically `customera / 12345678`.

## Running the REST API server

```bash
uv run uvicorn persfin.main:app --reload
# or
uv run persfin
```

The server starts at **http://localhost:8000**.  
Interactive API docs are available at **http://localhost:8000/docs**.

## REST API usage flow

### 1. List available Norwegian banks

```bash
curl http://localhost:8000/banks?country=NO
```

### 2. Start the bank authorisation

```bash
curl -s -X POST http://localhost:8000/connect \
  -H "Content-Type: application/json" \
  -d '{"aspsp_name": "Sbanken", "aspsp_country": "NO"}' | python -m json.tool
```

The response contains a `url` – open it in your browser.  
The bank sandbox login credentials are typically `customera / 12345678`.

### 3. Callback (automatic)

After you log in, the bank redirects to `http://localhost:8000/callback?code=…`.  
The app exchanges the code for a session automatically and shows the connected accounts.

### 4. Fetch accounts

```bash
curl http://localhost:8000/accounts
```

### 5. Fetch balances

```bash
curl http://localhost:8000/accounts/<account_uid>/balances
```

### 6. Fetch transactions

```bash
# Last 90 days (default)
curl http://localhost:8000/accounts/<account_uid>/transactions

# From a specific date
curl "http://localhost:8000/accounts/<account_uid>/transactions?date_from=2024-01-01"

# Next page (pagination)
curl "http://localhost:8000/accounts/<account_uid>/transactions?continuation_key=<key>"
```

## Project structure

```
src/persfin/
├── __init__.py          # package entry point
├── cli.py               # interactive CLI (bank picker + transaction printer)
├── config.py            # settings (pydantic-settings + .env)
├── enablebanking.py     # Enable Banking API client (httpx + JWT)
├── models.py            # Pydantic response models
└── main.py              # FastAPI application
```

## Development

```bash
# Lint
uv run ruff check src/

# Type-check
uv run mypy src/

# Tests
uv run pytest
```
