from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_id: str = "1ac8f792-e6bf-4a66-8512-244359e69fcf"
    pem_file: Path = Path("1ac8f792-e6bf-4a66-8512-244359e69fcf.pem")
    redirect_url: str = "http://localhost:8000/callback"
    api_origin: str = "https://api.enablebanking.com"

    # Norwegian bank defaults (override via .env)
    aspsp_name: str = "Sbanken"
    aspsp_country: str = "NO"


settings = Settings()
