"""A small Hindsight-compatible HTTP server for tests and offline rehearsal.

It implements the endpoints Precedent uses (create/delete bank, directives, retain, recall,
reflect) with the same paths and JSON shapes the official SDK expects, so the REAL
`hindsight-client` SDK (and its aiohttp transport) can be exercised end to end without
network access or credits. Retrieval is keyword + tag based, and each retained document is
split into two "facts" to mimic Hindsight's fact extraction fan-out.

It is NOT Hindsight: no LLM fact extraction, entity graph, temporal reasoning or synthesis.

Run standalone:   python -m tests.fake_hindsight_server --port 8888
then set HINDSIGHT_API_URL=http://localhost:8888 in .env.
"""
from __future__ import annotations

import argparse
import threading
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from app.memory.base import MemoryItem
from app.memory.local_backend import LocalMemory

app = Flask("fake-hindsight")
_lock = threading.Lock()
BANKS: dict[str, dict] = {}
REQUEST_LOG: list[tuple[str, str]] = []


def _bank(bank_id: str) -> dict:
    return BANKS.setdefault(bank_id, {"memory": LocalMemory(), "directives": [], "config": {}})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.before_request
def _log():
    REQUEST_LOG.append((request.method, request.path))


@app.put("/v1/default/banks/<bank_id>")
def create_bank(bank_id):
    with _lock:
        b = _bank(bank_id)
        b["config"].update(request.get_json(silent=True) or {})
    return jsonify({"bank_id": bank_id, "name": bank_id,
                    "disposition": {"skepticism": 3, "literalism": 3, "empathy": 3},
                    "mission": b["config"].get("reflect_mission", "")})


@app.delete("/v1/default/banks/<bank_id>")
def delete_bank(bank_id):
    with _lock:
        existed = BANKS.pop(bank_id, None) is not None
    if not existed:
        return jsonify({"detail": "Bank not found"}), 404
    return jsonify({"success": True, "message": "deleted"})


@app.get("/v1/default/banks/<bank_id>/directives")
def list_directives(bank_id):
    items = _bank(bank_id)["directives"]
    return jsonify({"items": items, "total": len(items), "limit": 100, "offset": 0})


@app.post("/v1/default/banks/<bank_id>/directives")
def create_directive(bank_id):
    body = request.get_json(force=True)
    d = {"id": uuid.uuid4().hex, "bank_id": bank_id, "name": body["name"], "content": body["content"],
         "priority": body.get("priority", 0), "is_active": body.get("is_active", True),
         "tags": body.get("tags"), "created_at": _now(), "updated_at": _now()}
    with _lock:
        _bank(bank_id)["directives"].append(d)
    return jsonify(d)


@app.post("/v1/default/banks/<bank_id>/memories")
def retain(bank_id):
    body = request.get_json(force=True)
    items = body.get("items") or []
    with _lock:
        mem: LocalMemory = _bank(bank_id)["memory"]
        for it in items:
            doc = it.get("document_id") or body.get("document_id") or uuid.uuid4().hex
            mem.items = [m for m in mem.items if m.document_id != doc]
            text = it["content"] if isinstance(it["content"], str) else str(it["content"])
            # Mimic fact extraction: two facts per document, same tags/metadata/document_id.
            for part in (text, f"Fact about {doc}: {text[:160]}"):
                mem.items.append(MemoryItem(id=uuid.uuid4().hex[:12], text=part,
                                            tags=list(it.get("tags") or []) + list(body.get("document_tags") or []),
                                            metadata=dict(it.get("metadata") or {}),
                                            timestamp=it.get("timestamp"), document_id=doc))
    return jsonify({"success": True, "bank_id": bank_id, "items_count": len(items), "async": False})


@app.post("/v1/default/banks/<bank_id>/memories/recall")
def recall(bank_id):
    body = request.get_json(force=True)
    with _lock:
        mem: LocalMemory = _bank(bank_id)["memory"]
        hits = mem.recall(body.get("query", ""), tags=body.get("tags"),
                          tags_match=body.get("tags_match", "any"), limit=60)
    results = [{"id": h.id, "text": h.text, "type": "world", "document_id": h.document_id,
                "metadata": h.metadata, "tags": h.tags, "occurred_start": h.timestamp,
                "mentioned_at": h.timestamp} for h in hits]
    return jsonify({"results": results})


@app.post("/v1/default/banks/<bank_id>/reflect")
def reflect(bank_id):
    body = request.get_json(force=True)
    tags = body.get("tags")
    with _lock:
        one_per_doc, seen = LocalMemory(), set()
        for m in _bank(bank_id)["memory"].items:  # summarise cases, not extracted facts
            if m.document_id not in seen:
                seen.add(m.document_id)
                one_per_doc.items.append(m)
        r = one_per_doc.reflect(body.get("query", ""), tags=tags)
    out = {"text": r.text, "based_on": {"memories": [{"text": t} for t in r.based_on[:10]]}}
    if body.get("response_schema"):
        patterns = [{"exception_type": p["exception_type"], "usual_resolution": p["usual_resolution"],
                     "tolerance_or_rule": p["detail"], "evidence_count": p["occurrences"]}
                    for p in (r.structured or {}).get("patterns", [])]
        out["structured_output"] = {"summary": "Summary built from remembered cases (test server).",
                                    "patterns": patterns, "watch_outs": []}
    return jsonify(out)


class ServerThread:
    """Run the fake server on a free localhost port inside the test process."""

    def __init__(self, port: int = 0):
        self.server = make_server("127.0.0.1", port, app, threaded=True)
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()


def reset_state():
    with _lock:
        BANKS.clear()
        REQUEST_LOG.clear()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8888)
    a = ap.parse_args()
    print(f"Fake Hindsight on http://localhost:{a.port}  (test server, not the real service)")
    app.run(host="127.0.0.1", port=a.port, threaded=True)
