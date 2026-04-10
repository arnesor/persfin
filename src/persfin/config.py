from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_id: str = "9a83a4b1-eb9f-4b87-8314-fe1e2d6746b1"
    pem_file: Path = Path("9a83a4b1-eb9f-4b87-8314-fe1e2d6746b1.pem")
    redirect_url: str = "https://localhost:8000/callback"
    api_origin: str = "https://api.enablebanking.com"

    # Norwegian bank defaults (override via .env)
    aspsp_name: str = "Sbanken"
    aspsp_country: str = "NO"


settings = Settings()
