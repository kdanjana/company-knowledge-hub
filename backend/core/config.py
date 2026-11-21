"""Settings for the core pipeline, read from environment variables (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    value = _str(name)
    return int(value) if value else default


def _float(name: str, default: float) -> float:
    value = _str(name)
    return float(value) if value else default


def _optional_float(name: str) -> float | None:
    value = _str(name)
    return float(value) if value else None


@dataclass(frozen=True)
class Config:
    aws_region: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: str
    chat_model_id: str
    embed_model_id: str
    embed_dim: int
    chat_temperature: float | None
    price_input_per_1k: float
    price_output_per_1k: float
    top_k: int
    min_similarity: float
    max_pairs: int
    investigator_max_steps: int
    kb_database_url: str
    chat_top_k: int
    chat_min_similarity: float

    def require_bedrock(self) -> None:
        """Fail early with a clear message if the model IDs are not filled in."""
        missing = [
            name
            for name, value in (("CHAT_MODEL_ID", self.chat_model_id), ("EMBED_MODEL_ID", self.embed_model_id))
            if not value
        ]
        if missing:
            raise ValueError(
                f"Missing in .env: {', '.join(missing)}. See README step 3 (choose models available in your region)."
            )


def _default_kb_url() -> str:
    user = _str("POSTGRES_USER", "kb_auditor")
    password = _str("POSTGRES_PASSWORD", "")
    host = _str("POSTGRES_HOST", "localhost")
    port = _str("POSTGRES_PORT", "5432")
    db = _str("POSTGRES_DB", "kb_auditor")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def load_config() -> Config:
    return Config(
        aws_region=_str("AWS_REGION", "ap-south-1"),
        aws_access_key_id=_str("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_str("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=_str("AWS_SESSION_TOKEN"),
        chat_model_id=_str("CHAT_MODEL_ID"),
        embed_model_id=_str("EMBED_MODEL_ID"),
        embed_dim=_int("EMBED_DIM", 1024),
        chat_temperature=_optional_float("CHAT_TEMPERATURE"),
        price_input_per_1k=_float("CHAT_PRICE_INPUT_PER_1K", 0.0),
        price_output_per_1k=_float("CHAT_PRICE_OUTPUT_PER_1K", 0.0),
        top_k=_int("CANDIDATE_TOP_K", 5),
        min_similarity=_float("CANDIDATE_MIN_SIMILARITY", 0.5),
        max_pairs=_int("MAX_PAIRS_PER_RUN", 300),
        investigator_max_steps=_int("INVESTIGATOR_MAX_STEPS", 4),
        kb_database_url=_str("KB_DATABASE_URL") or _default_kb_url(),
        chat_top_k=_int("CHAT_TOP_K", 5),
        chat_min_similarity=_float("CHAT_MIN_SIMILARITY", 0.3),
    )
