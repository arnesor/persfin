# persfin

## Commands

- Use Python 3.13+ and `uv`; install the locked environment with `uv sync --all-groups`.
- Run the API with `uv run uvicorn persfin.main:app --reload` or `uv run persfin`.
- Run the interactive bank-import CLI with `uv run persfin-cli [--from-date YYYY-MM-DD]`.
- Verify changes with `uv run ruff check src tests`, `uv run mypy src`, and `uv run pytest`. Run one test with `uv run pytest tests/test_main.py::TestConnect::test_returns_auth_url`.

## Application Boundaries

- The importable package is `src/persfin`; `persfin.main:app` includes the routers in `api/`, while `persfin.cli:main` is the CLI entry point.
- Keep Enable Banking HTTP/JWT logic in `services/enablebanking.py`; API routers should validate/coordinate requests and use the shared `httpx.HTTPError` handler in `main.py` for upstream failures.
- Active API sessions are process-local in `core/session_store.py` and selected by the secure `session_id` cookie. They do not persist across server restarts.
- The CLI starts its own HTTPS callback server on `127.0.0.1:8000`, requires `firefly/certs/localhost+2.pem` and `localhost+2-key.pem`, persists session tokens in `~/.persfin/session_cache_<APP_ID>.json`, and writes CSV exports to `data/`.

## Configuration And Testing

- `core.config.Settings` reads `.env` from the working directory. `APP_ID` and `PEM_FILE` are required; copy `.env.example` and keep the Enable Banking PEM key at the repository root. Do not expose or commit either secret.
- Tests use FastAPI's synchronous `TestClient`, mock all Enable Banking calls at the router import site, and install a fresh `SessionStore` through `app.dependency_overrides[get_store]` for every test. Preserve those patterns to avoid network calls and state leakage.
- Ruff targets Python 3.13, checks annotations and Google-style public docstrings, and formats code; tests have annotation/docstring exceptions configured in `pyproject.toml`.

## Firefly Stack

- `firefly/` is a separate Docker Compose integration. Copy each `*.example` environment file before running `docker compose -f docker-compose.yml up -d --pull=always` from that directory.
