from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "BlogBot IA"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    secret_key: str = "change-me"
    admin_username: str = "admin"
    admin_password: str = "Admin123*"
    database_url: str = "postgresql+psycopg://postgres:Admin@localhost:5432/blogbot_ia"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen4:2b"
    openclaw_base_url: str = "http://127.0.0.1:4100"
    openclaw_enabled: bool = False
    pexels_api_key: str = ""
    unsplash_access_key: str = ""
    openverse_base_url: str = "https://api.openverse.org"
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""
    public_base_url: str = "http://127.0.0.1:8000"
    enable_auto_embeddings: bool = True
    csrf_header_name: str = "X-CSRF-Token"
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_cpu_threads: int = 4
    stt_language: str = "es"
    kokoro_lang_code: str = "e"
    kokoro_voice: str = "ef_dora"
    generated_blogs_path: Path = Field(default=BASE_DIR / "generated_blogs")
    uploads_path: Path = Field(default=BASE_DIR / "uploads")
    logs_path: Path = Field(default=BASE_DIR / "logs")
    model_cache_path: Path = Field(default=BASE_DIR / ".model_cache")

    @property
    def allowed_chat_ids(self) -> set[int]:
        values = {value.strip() for value in self.telegram_allowed_chat_ids.split(",") if value.strip()}
        return {int(value) for value in values}


@lru_cache
def get_settings() -> Settings:
    return Settings()
