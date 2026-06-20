# persfin

Personal finance code that fetches European bank transactions via [Enable Banking].

It also includes a [Firefly III] Docker setup and integration for importing
transactions into Firefly III, using Enable Banking.

## Requirements

- Python 3.13+
- [uv] package manager.
- A sandbox or production app registered at [Enable Banking].
- The private key `.pem` file downloaded when registering the Enable Banking app, placed in the project root.
- A self-signed certificate for https.

## Configure Enable Banking

1. Register a user at [Enable Banking].
2. Click on your profile name and select API applications.

### Sandbox app

- Add a new sandbox application.
   - Select Generate a private RSA key in the browser.
   - Enter an application name.
   - Allowed redirect URLs:
     - http://localhost:8000/callback
     - https://localhost:8000/callback
     - https://importer.localhost/eb-callback
   - Download the `.pem` file and store it in the root directory of the repo.
   - The application ID is the first part of the `.pem` file name.

### Production app

1. Add a new production application.
   - Select Generate a private RSA key in the browser.
   - Enter an application name.
   - Allowed redirect URLs:
      - https://localhost:8000/callback
      - https://importer.localhost/eb-callback
   - Application description: App for getting personal transactions
   - Email for data protection matters: Your email address
   - Privacy URL: https://arnesor.github.io/persfin/privacy
   - Terms URL: https://arnesor.github.io/persfin/terms
   - Download the `.pem` file and store it in the root directory of the repo.
   - The application ID is the first part of the `.pem` file name.
2. Link accounts
   - For each bank account you want to access:
      - Click on the Link accounts button.
      - Select country and bank name. Set usage type to Personal.
      - Click on the Link button.

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

## Quick start – interactive CLI

The easiest way to use persfin is the interactive CLI.
It lists all Norwegian banks, lets you pick one, opens the bank login in your
browser, and then prints your balances and recent transactions and stores to csv file.

### Configure
1. Copy `.env.example` in the root directory to `.env`.
2. Open the `.env` file, and set the Enable Banking application ID 
   and `.pem`-file to the one you downloaded from Enable Banking. 

### Run

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
curl https://localhost:8000/accounts/<account_uid>/transactions

# From a specific date
curl "https://localhost:8000/accounts/<account_uid>/transactions?date_from=2024-01-01"

# Next page (pagination)
curl "https://localhost:8000/accounts/<account_uid>/transactions?continuation_key=<key>"
```

## Project structure

```
src/persfin/
├── main.py                  # slim: app + exception handler + router includes
├── schemas.py               # all Pydantic models in one file
├── core/
│   ├── config.py            # pydantic-settings (was persfin/config.py)
│   └── session_store.py     # SessionStore + get_store + StoreDep (was in main.py)
├── services/
│   └── enablebanking.py     # HTTP client (was persfin/enablebanking.py)
└── api/
    ├── banks.py             # GET /banks
    ├── auth.py              # POST /connect, GET /callback
    └── accounts.py          # GET /accounts + balances + transactions

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

Requirement: A working docker installation.

### Configuration

Go to the `firefly` directory and copy all `.example` files to the same
filename, without the `.example` extension.

Edit the following variables in the files:

`.db.env`: Change the MYSQL_PASSWORD to your own password.

`.env`: Change the DB_PASSWORD to the same as the MYSQL_PASSWORD.

`.importer.env`: Set FIREFLY_III_ACCESS_TOKEN to the personal access token
generated by following
[this description](https://docs.firefly-iii.org/how-to/firefly-iii/features/api/).

Start the containers, in the firefly directory:

```shell
docker compose -f docker-compose.yml up -d --pull=always
```

Open the Firefly III web interface at https://firefly.localhost/

Open the Data Importer web interface at https://importer.localhost/

Showing logs:

```shell
docker compose -f docker-compose.yml logs -f
````
Shutting down the containers:

```shell
docker compose -f docker-compose.yml down
```

[Enable Banking]: https://www.enablebanking.com
[Firefly III]: https://www.firefly-iii.org/
[mkcert]: https://github.com/FiloSottile/mkcert
[uv]: https://docs.astral.sh/uv/