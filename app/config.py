import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./hiring_assessment.db")
    audio_storage_dir: Path = Path(os.getenv("AUDIO_STORAGE_DIR", "./var/audio"))
    hr_demo_username: str = os.getenv("HR_DEMO_USERNAME", "hr-demo")
    hr_demo_password: str = os.getenv("HR_DEMO_PASSWORD", "change-me")
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    llm_api_key: str | None = os.getenv("LLM_API_KEY")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    max_audio_bytes: int = int(os.getenv("MAX_AUDIO_BYTES", "25000000"))
    candidate_token_days: int = 180
    hr_session_hours: int = 8
    worker_lease_seconds: int = 120
    worker_max_attempts: int = 3


settings = Settings()
