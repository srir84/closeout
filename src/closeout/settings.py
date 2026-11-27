"""All settings, read from the environment (design 3.14)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    closeout_mode: Literal["replay", "light", "full", "hosted"] = "replay"
    closeout_time_scale: float = 1.0
    database_url: str = "postgresql+psycopg://closeout:closeout@postgres:5432/closeout"
    temporal_address: str = "temporal:7233"
    temporal_namespace: str = "default"
    app_public_url: str = "http://127.0.0.1:8000"
    app_internal_url: str = "http://app:8000"
    allowed_hosts: str = "127.0.0.1:8000,localhost:8000,app:8000,127.0.0.1,localhost,app"
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    mailpit_api_url: str = "http://mailpit:8025"
    sender_lease_seconds: float = 120
    smtp_timeout_seconds: float = 30
    sender_poll_seconds: float = 0.5
    closeout_keys_dir: Path = Path(".secrets")
    uploads_dir: Path = Path("var/uploads")
    ollama_url: str = "http://host.docker.internal:11434"
    reader_a: str = "qwen3-vl:4b-instruct"
    reader_b: str = "qwen3-vl:8b-instruct"
    hosted_api_key: str = ""
    hosted_model: str = ""
    hosted_base_url: str = "https://api.openai.com/v1"
    closeout_traces: Literal["on", "off"] = "off"
    sweep_seconds: float = 30
    replay_dir: Path = Path("evals/replay")
    task_queue: str = "closeout"
    model_task_queue: str = "model-reads"

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
