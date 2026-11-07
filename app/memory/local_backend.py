"""Offline stand-ins for Hindsight.

LocalMemory  - same retain/recall/reflect contract, keyword + tag retrieval, JSON persistence.
               Used by the test-suite and when no network is available. NOT a Hindsight
               replacement: no fact extraction, entity graph, temporal search or LLM reflection.
NullMemory   - remembers nothing. The "memory OFF" baseline for the before/after demo.
"""
from __future__ import annotations

import json
import math
import re
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from .base import MemoryItem, Reflection, parse_tags, tags_ok

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class LocalMemory:
    name = "local"

    def __init__(self, path: Path | None = None):
        self.path = path
        self.items: list[MemoryItem] = []
        if path and path.exists():
            self.items = [MemoryItem(**d) for d in json.loads(path.read_text(encoding="utf-8"))]

    def setup(self) -> None:
        pass

    def reset(self) -> None:
        self.items = []
        self._save()

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([i.to_dict() for i in self.items], indent=1), encoding="utf-8")

    def retain(self, content, *, context, timestamp=None, tags=None, metadata=None, document_id=None):
        if document_id:  # same semantics as Hindsight: re-retaining a document replaces it
            self.items = [i for i in self.items if i.document_id != document_id]
        self.items.append(MemoryItem(id=uuid.uuid4().hex[:12], text=content, tags=list(tags or []),
                                     metadata={k: str(v) for k, v in (metadata or {}).items()},
                                     timestamp=timestamp, document_id=document_id))
        self._save()

    def recall(self, query, *, tags=None, tags_match="any", limit=12):
        pool = [i for i in self.items if tags_ok(i.tags, tags, tags_match)]
        if not pool:
            return []
        q = Counter(_tokens(query))
        df = Counter(t for i in pool for t in set(_tokens(i.text)))
        n = len(pool)
        scored = []
        for idx, item in enumerate(pool):
            toks = Counter(_tokens(item.text))
            s = sum(math.log(1 + n / df[t]) * min(toks[t], 3) for t in q if t in toks)
            s += 0.002 * idx  # mild recency preference (items are appended chronologically)
            scored.append((s, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for s, item in scored[:limit]:
            out.append(MemoryItem(**{**item.to_dict(), "score": round(s, 3)}))
        return out

    def reflect(self, query, *, tags=None, response_schema=None):
        """Deterministic summary of the tagged cases (no LLM)."""
        pool = [i for i in self.items if tags_ok(i.tags, tags, "any_strict" if tags else "any")]
        cases = [i for i in pool if "action" in i.tagmap]
        if not pool:
            return Reflection(text="No history yet for this scope. Every exception will go to a human "
                                   "until the team has resolved a few.", structured={"patterns": []})
        by_type: dict[str, list[MemoryItem]] = defaultdict(list)
        for c in cases:
            by_type[c.tagmap.get("exception", "other")].append(c)
        patterns, lines = [], []
        for etype, cs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
            actions = Counter(c.tagmap["action"] for c in cs)
            mags = defaultdict(list)
            for c in cs:
                try:
                    mags[c.tagmap["action"]].append(float(c.tagmap.get("mag", "0")))
                except ValueError:
                    pass
            parts = []
            for a, k in actions.most_common():
                rng = mags.get(a) or [0]
                band = (f" (seen {min(rng):g}–{max(rng):g})" if max(rng) > 0 else "")
                parts.append(f"{a} ×{k}{band}")
            latest = cs[-1].metadata.get("reason", "")
            patterns.append({"exception_type": etype, "occurrences": len(cs),
                             "usual_resolution": actions.most_common(1)[0][0],
                             "detail": "; ".join(parts), "latest_reason": latest})
            lines.append(f"- **{etype}** — {len(cs)} cases: {'; '.join(parts)}. Latest reason: {latest}")
        notes = [i.text for i in pool if i.tagmap.get("kind") == "vendor_email"]
        text = "\n".join(lines) + ("\n\nVendor correspondence on file:\n" + "\n".join(f"- {n}" for n in notes)
                                   if notes else "")
        return Reflection(text=text, structured={"patterns": patterns, "notes": notes},
                          based_on=[c.id for c in cases])


class NullMemory:
    name = "off"

    def setup(self): pass

    def reset(self): pass

    def retain(self, *a, **k): pass

    def recall(self, *a, **k): return []

    def reflect(self, *a, **k):
        return Reflection(text="Memory is off.", structured={"patterns": []})


__all__ = ["LocalMemory", "NullMemory", "parse_tags"]
