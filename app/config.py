"""Runtime configuration.

The .env file in the project folder is the single source of truth. Values in .env override
variables left over in your terminal (v1 let a stray `MEMORY_BACKEND=local` in PowerShell
silently win over .env). Set PRECEDENT_IGNORE_DOTENV=1 to skip .env (the tests do this).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "2.0"
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RUNTIME_DIR = ROOT / "runtime"
ENV_FILE = ROOT / ".env"

PLACEHOLDERS = ("hsk_your_key_here", "gsk_your_key_here", "paste_your")


def read_dotenv(path: Path = ENV_FILE) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    # utf-8-sig: Notepad on Windows may save a BOM
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        out[key.strip()] = value
    return out


def _load() -> dict[str, str]:
    if os.getenv("PRECEDENT_IGNORE_DOTENV") == "1":
        return {}
    values = read_dotenv()
    for k, v in values.items():
        os.environ[k] = v  # .env wins over stale terminal variables
    return values


DOTENV = _load()


def _get(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key)
    if v is None or v.strip() == "" or any(v.startswith(p) for p in PLACEHOLDERS):
        return default
    return v.strip()


@dataclass(frozen=True)
class Settings:
    memory_backend: str = field(default_factory=lambda: (_get("MEMORY_BACKEND", "hindsight") or "hindsight").lower())
    hindsight_url: str = field(default_factory=lambda: _get("HINDSIGHT_API_URL", "https://api.hindsight.vectorize.io"))
    hindsight_api_key: str | None = field(default_factory=lambda: _get("HINDSIGHT_API_KEY"))
    bank_id: str = field(default_factory=lambda: _get("HINDSIGHT_BANK_ID", "precedent-deccan-fresh-ap"))
    llm_api_key: str | None = field(default_factory=lambda: _get("GROQ_API_KEY") or _get("LLM_API_KEY"))
    llm_base_url: str = field(default_factory=lambda: _get("LLM_BASE_URL", "https://api.groq.com/openai/v1"))
    llm_model: str = field(default_factory=lambda: _get("LLM_MODEL", "openai/gpt-oss-120b"))
    llm_fallback_model: str = field(default_factory=lambda: _get("LLM_FALLBACK_MODEL", "qwen/qwen3-32b"))
    port: int = field(default_factory=lambda: int(_get("PORT", "8000")))


settings = Settings()
