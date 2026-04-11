from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_id: str = "967a2989-7e4f-453c-b9eb-08c19a9f64c5"
    pem_file: Path = Path("967a2989-7e4f-453c-b9eb-08c19a9f64c5.pem")
    redirect_url: str = "https://localhost:8000/callback"
    api_origin: str = "https://api.enablebanking.com"

    # Norwegian bank defaults (override via .env)
    aspsp_name: str = "Sbanken"
    aspsp_country: str = "NO"


settings = Settings()
