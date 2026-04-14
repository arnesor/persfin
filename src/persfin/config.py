import functools
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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
