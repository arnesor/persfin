"""Application settings loaded from environment / .env file."""

import functools
from datetime import date
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, populated from environment variables or a .env file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_id: str
    pem_file: Path
    redirect_url: str = "https://localhost:8000/callback"
    api_origin: str = "https://api.enablebanking.com"

    # Norwegian bank defaults (override via .env)
    aspsp_name: str = "Sbanken"
    aspsp_country: str = "NO"

    # PocketSmith is optional and only required by ``persfin-cli --pocketsmith``.
    pocketsmith_developer_key: SecretStr | None = None
    pocketsmith_transaction_account_id: int | None = None
    pocketsmith_source_iban: str = "NO11111111111"
    pocketsmith_cutover_date: date | None = None
    pocketsmith_api_origin: str = "https://api.pocketsmith.com/v2"


@functools.cache
def get_settings() -> Settings:
    """Return the application settings, cached after the first call.

    Tests can supply alternative settings via::

        app.dependency_overrides[get_settings] = lambda: Settings(
            app_id="test",
            pem_file=Path("/dev/null"),
            redirect_url="https://localhost:8000/callback",
        )

    Call ``get_settings.cache_clear()`` to force a fresh read (e.g. after
    changing env vars in a test).
    """
    return Settings()
