from __future__ import annotations

from ..config import RUNTIME_DIR, Settings
from .base import MemoryBackend, MemoryItem, Reflection, parse_tags
from .local_backend import LocalMemory, NullMemory


def build_memory(settings: Settings) -> MemoryBackend:
    if settings.memory_backend == "hindsight":
        from .hindsight_backend import HindsightMemory
        return HindsightMemory(settings.hindsight_url, settings.hindsight_api_key, settings.bank_id,
                               state_dir=RUNTIME_DIR)
    if settings.memory_backend == "local":
        return LocalMemory(RUNTIME_DIR / "local_memory.json")
    raise ValueError(f"Unknown MEMORY_BACKEND={settings.memory_backend!r} (use 'hindsight' or 'local')")


__all__ = ["build_memory", "MemoryBackend", "MemoryItem", "Reflection", "LocalMemory", "NullMemory",
           "parse_tags"]
