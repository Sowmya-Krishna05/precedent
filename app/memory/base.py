"""Memory backend interface.

Precedent is written against three Hindsight operations:
  retain  - store an experience (a resolved exception, a vendor email, an override)
  recall  - fetch the precedents relevant to a new exception (tag-scoped)
  reflect - synthesise a vendor playbook / controller brief from everything remembered

`HindsightMemory` is the production backend. `LocalMemory` mirrors the same interface
offline so the test-suite and CI run without network access. `NullMemory` is the
"memory OFF" baseline used for the before/after comparison in the demo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class MemoryItem:
    id: str
    text: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    timestamp: str | None = None
    document_id: str | None = None
    score: float | None = None

    @property
    def tagmap(self) -> dict[str, str]:
        return parse_tags(self.tags)

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "tags": self.tags, "metadata": self.metadata,
                "timestamp": self.timestamp, "document_id": self.document_id, "score": self.score}


@dataclass
class Reflection:
    text: str
    structured: dict[str, Any] | None = None
    based_on: list[str] = field(default_factory=list)


class MemoryBackend(Protocol):
    name: str

    def setup(self) -> None: ...

    def reset(self) -> None: ...

    def retain(self, content: str, *, context: str, timestamp: str | None = None,
               tags: list[str] | None = None, metadata: dict[str, str] | None = None,
               document_id: str | None = None) -> None: ...

    def recall(self, query: str, *, tags: list[str] | None = None, tags_match: str = "any",
               limit: int = 12) -> list[MemoryItem]: ...

    def reflect(self, query: str, *, tags: list[str] | None = None,
                response_schema: dict | None = None) -> Reflection: ...


def parse_tags(tags: list[str] | None) -> dict[str, str]:
    """['vendor:V001', 'action:approve'] -> {'vendor': 'V001', 'action': 'approve'}"""
    out: dict[str, str] = {}
    for t in tags or []:
        if ":" in t:
            k, _, v = t.partition(":")
            out.setdefault(k, v)
    return out


def tags_ok(item_tags: list[str], want: list[str] | None, mode: str) -> bool:
    """Same semantics as Hindsight's tags_match."""
    if not want:
        return True
    have = set(item_tags or [])
    if not have:
        return mode in ("any", "all")
    w = set(want)
    if mode in ("any", "any_strict"):
        return bool(have & w)
    if mode in ("all", "all_strict"):
        return w <= have
    if mode == "exact":
        return have == w
    raise ValueError(mode)
