# persfin

Personal finance app that fetches European bank transactions via [Enable Banking].

## Requirements

- Python 3.13+
- [uv] package manager.
- A sandbox or production app registered at [Enable Banking].
- The private key `.pem` file downloaded when registering the Enable Banking app, placed in the project root.
- A self-signed certificate for https.

## Configure Enable Banking
1. Register a user at [Enable Banking].
2. Click on your profile name and select API applications.
3. Add a new sandbox application.
   - Select Generate a private RSA key in the browser.
   - Enter an application name.
   - Allowed redirect URLs:
     - http://localhost:8000/callback
     - https://localhost:8000/callback
     - https://importer.localhost/eb-callback
   - Download the `.pem` file and store it in the root directory of the repo.
   - The application ID is the first part of the `.pem` file name.

## Setup a self signed certificate for https

1. Install mkcert:

| Platform | Command                              |
|---|---------------------------------------------|
| Windows | `winget install FiloSottile.mkcert`   |
| Linux | `sudo apt install mkcert libnss3-tools` |
| macOS | `brew install mkcert`                   |

And then: 

```shell
mkcert -install
```

2. Create a self-signed certificate:

Run this from the `firefly/certs/` directory:

```shell
mkcert localhost importer.localhost firefly.localhost
```

This produces `localhost+2.pem` (certificate) and `localhost+2-key.pem` (private key).

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
| `APP_ID`         | `967a2989-7e4f-453c-b9eb-08c19a9f64c5`     | Enable Banking application ID            |
| `PEM_FILE`       | `967a2989-7e4f-453c-b9eb-08c19a9f64c5.pem` | Path to the RSA private key              |
| `REDIRECT_URL`   | `https://localhost:8000/callback`          | OAuth callback URL (must be whitelisted) |
| `API_ORIGIN`     | `https://api.enablebanking.com`            | Enable Banking API base URL              |
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

## Firefly III integration

```shell
docker compose -f docker-compose.yml up -d --pull=always
docker compose -f docker-compose.yml logs -f
docker compose -f docker-compose.yml down
```

[Enable Banking]: (https://www.enablebanking.com)
[mkcert]: (https://github.com/FiloSottile/mkcert)
[uv]: (https://docs.astral.sh/uv/)