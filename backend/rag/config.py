from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Load the repository-level environment file regardless of launch directory.
load_dotenv(PROJECT_ROOT / ".env")
DATA_DIR = Path(
    os.getenv("STUDY_DATA_DIR", str(PROJECT_ROOT / "data"))
).expanduser().resolve()

DATABASE_PATH = DATA_DIR / "app.db"

CHROMA_PATH = DATA_DIR / "chroma"
MEMORY_CHROMA_PATH = DATA_DIR / "memory_chroma"


# ============================================================
# PERSISTENCE BACKEND
# ============================================================

PERSISTENCE_BACKEND = os.getenv(
    "PERSISTENCE_BACKEND",
    "sqlite",
).strip().lower()

SUPPORTED_PERSISTENCE_BACKENDS = {
    "sqlite",
    "cockroach",
}

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATABASE_POOL_SIZE = int(os.getenv("DATABASE_POOL_SIZE", "5"))
DATABASE_MAX_OVERFLOW = int(os.getenv("DATABASE_MAX_OVERFLOW", "5"))
DATABASE_CONNECT_TIMEOUT = int(os.getenv("DATABASE_CONNECT_TIMEOUT", "15"))
DATABASE_MAX_TRANSACTION_RETRIES = int(
    os.getenv("DATABASE_MAX_TRANSACTION_RETRIES", "5")
)
DATABASE_RETRY_BASE_DELAY_MS = int(
    os.getenv("DATABASE_RETRY_BASE_DELAY_MS", "100")
)
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "384"))
ENABLE_VECTOR_INDEX = (
    os.getenv("ENABLE_VECTOR_INDEX", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)

ALLOW_LEGACY_DEFAULT_WORKSPACE = (
    os.getenv("ALLOW_LEGACY_DEFAULT_WORKSPACE", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)
GUEST_SESSION_TOKEN_PEPPER = os.getenv(
    "GUEST_SESSION_TOKEN_PEPPER",
    "",
).strip()
_GUEST_SESSION_TTL_DAYS_RAW = os.getenv(
    "GUEST_SESSION_TTL_DAYS",
    "",
).strip()
GUEST_SESSION_TTL_DAYS = (
    int(_GUEST_SESSION_TTL_DAYS_RAW)
    if _GUEST_SESSION_TTL_DAYS_RAW
    else None
)
GUEST_SESSION_LAST_SEEN_MINUTES = int(
    os.getenv("GUEST_SESSION_LAST_SEEN_MINUTES", "5")
)
GUEST_SESSION_CREATION_LIMIT_PER_MINUTE = int(
    os.getenv("GUEST_SESSION_CREATION_LIMIT_PER_MINUTE", "30")
)
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "").strip().rstrip("/")


def validate_persistence_config() -> None:
    """Fail before startup when the selected backend is unsafe or incomplete."""
    if PERSISTENCE_BACKEND not in SUPPORTED_PERSISTENCE_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_PERSISTENCE_BACKENDS))
        raise RuntimeError(
            "Unsupported PERSISTENCE_BACKEND. "
            f"Supported values: {supported}."
        )
    if PERSISTENCE_BACKEND == "cockroach" and not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is required when PERSISTENCE_BACKEND=cockroach."
        )
    numeric_settings = {
        "DATABASE_POOL_SIZE": DATABASE_POOL_SIZE,
        "DATABASE_MAX_OVERFLOW": DATABASE_MAX_OVERFLOW,
        "DATABASE_CONNECT_TIMEOUT": DATABASE_CONNECT_TIMEOUT,
        "DATABASE_MAX_TRANSACTION_RETRIES": DATABASE_MAX_TRANSACTION_RETRIES,
        "DATABASE_RETRY_BASE_DELAY_MS": DATABASE_RETRY_BASE_DELAY_MS,
        "EMBEDDING_DIMENSION": EMBEDDING_DIMENSION,
    }
    for name, value in numeric_settings.items():
        minimum = 0 if name in {
            "DATABASE_MAX_OVERFLOW",
            "DATABASE_MAX_TRANSACTION_RETRIES",
            "DATABASE_RETRY_BASE_DELAY_MS",
        } else 1
        if value < minimum:
            raise RuntimeError(f"{name} must be at least {minimum}.")
    if PERSISTENCE_BACKEND == "cockroach" and EMBEDDING_DIMENSION != 384:
        raise RuntimeError(
            "CockroachDB schema requires EMBEDDING_DIMENSION=384."
        )
    if GUEST_SESSION_TTL_DAYS is not None and not (
        1 <= GUEST_SESSION_TTL_DAYS <= 3650
    ):
        raise RuntimeError(
            "GUEST_SESSION_TTL_DAYS must be empty or between 1 and 3650."
        )
    if GUEST_SESSION_LAST_SEEN_MINUTES < 1:
        raise RuntimeError(
            "GUEST_SESSION_LAST_SEEN_MINUTES must be at least 1."
        )
    if GUEST_SESSION_CREATION_LIMIT_PER_MINUTE < 1:
        raise RuntimeError(
            "GUEST_SESSION_CREATION_LIMIT_PER_MINUTE must be at least 1."
        )


def guest_session_configured() -> bool:
    return len(GUEST_SESSION_TOKEN_PEPPER.encode("utf-8")) >= 32


def validate_guest_session_config() -> None:
    if ALLOW_LEGACY_DEFAULT_WORKSPACE:
        return
    if not guest_session_configured():
        raise RuntimeError(
            "GUEST_SESSION_TOKEN_PEPPER must contain at least 32 bytes "
            "when legacy workspace access is disabled."
        )


# ============================================================
# CHROMA COLLECTIONS
# ============================================================

CHROMA_COLLECTION = "study_documents"
MEMORY_CHROMA_COLLECTION = "learner_memories"


# ============================================================
# LLM PROVIDER
# ============================================================

LLM_PROVIDER = os.getenv(
    "LLM_PROVIDER",
    "openrouter",
).strip().lower()

LLM_API_KEY = os.getenv(
    "LLM_API_KEY",
    "",
).strip()

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "",
).strip()

LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL",
    "",
).strip()


SUPPORTED_LLM_PROVIDERS = {
    "openrouter",
    "openai_compatible",
    "groq",
}


def validate_llm_config() -> None:
    """
    Validate the configured LLM provider before creating a model.
    """
    if LLM_PROVIDER not in SUPPORTED_LLM_PROVIDERS:
        supported = ", ".join(
            sorted(SUPPORTED_LLM_PROVIDERS)
        )

        raise RuntimeError(
            f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}. "
            f"Supported providers: {supported}"
        )

    if not LLM_API_KEY:
        raise RuntimeError(
            "LLM_API_KEY is missing from .env."
        )

    if not LLM_MODEL:
        raise RuntimeError(
            "LLM_MODEL is missing from .env."
        )

    if (
        LLM_PROVIDER == "openai_compatible"
        and not LLM_BASE_URL
    ):
        raise RuntimeError(
            "LLM_BASE_URL is required when "
            "LLM_PROVIDER=openai_compatible."
        )
# ============================================================
# EMBEDDING MODEL
# ============================================================

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
).strip()


# ============================================================
# DOCUMENT RAG SETTINGS
# ============================================================

CHUNK_SIZE = int(
    os.getenv("CHUNK_SIZE", "1000")
)

CHUNK_OVERLAP = int(
    os.getenv("CHUNK_OVERLAP", "200")
)

RETRIEVAL_K = int(
    os.getenv("RETRIEVAL_K", "5")
)


# ============================================================
# MEMORY RETRIEVAL SETTINGS
# ============================================================

MEMORY_RETRIEVAL_K = int(
    os.getenv("MEMORY_RETRIEVAL_K", "5")
)

MAX_MEMORY_DISTANCE = float(
    os.getenv("MAX_MEMORY_DISTANCE", "1.15")
)


# ============================================================
# MEMORY PROPOSAL SETTINGS
# ============================================================

ENABLE_MEMORY_PROPOSALS = (
    os.getenv(
        "ENABLE_MEMORY_PROPOSALS",
        "true",
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)

MEMORY_PROPOSAL_MIN_CONFIDENCE = float(
    os.getenv(
        "MEMORY_PROPOSAL_MIN_CONFIDENCE",
        "0.75",
    )
)

MEMORY_PROPOSAL_MIN_IMPORTANCE = float(
    os.getenv(
        "MEMORY_PROPOSAL_MIN_IMPORTANCE",
        "0.40",
    )
)


# ============================================================
# MEMORY DUPLICATE DETECTION
# ============================================================

MEMORY_DUPLICATE_MAX_DISTANCE = float(
    os.getenv(
        "MEMORY_DUPLICATE_MAX_DISTANCE",
        "0.40",
    )
)


# ============================================================
# DIRECTORY INITIALIZATION
# ============================================================

def ensure_directories() -> None:
    """
    Create all required local data directories.
    """
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    CHROMA_PATH.mkdir(
        parents=True,
        exist_ok=True,
    )

    MEMORY_CHROMA_PATH.mkdir(
        parents=True,
        exist_ok=True,
    )

LLM_REASONING_VISIBLE = (
    os.getenv(
        "LLM_REASONING_VISIBLE",
        "false",
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)

MAX_UPLOAD_BYTES = int(
    os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
)
