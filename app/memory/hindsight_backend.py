"""Hindsight-backed memory (production path), using the official `hindsight-client` SDK.

Threading model (why this file looks the way it does)
-----------------------------------------------------
The SDK talks HTTP through aiohttp. An aiohttp session belongs to the event loop it was
created on, and the SDK creates it lazily on first use. A web server like Flask answers
each request on a different thread, so calling the SDK's *sync* wrappers from request
threads binds the session to one thread's loop and then fails on the next thread with
"RuntimeError: Timeout context manager should be used inside a task".

So this backend owns exactly one event loop, running forever on one daemon thread, and
submits the SDK's native *async* methods (arecall, aretain, areflect, ...) to it with
`asyncio.run_coroutine_threadsafe`. Any thread can call us; every HTTP call happens on the
same loop. This is verified in tests/test_hindsight_real_sdk.py against the real SDK.
"""
from __future__ import annotations

import asyncio
import atexit
import logging
import re
import threading
import uuid
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path

from .base import MemoryItem, Reflection

log = logging.getLogger(__name__)

RETAIN_MISSION = (
    "This bank is the institutional memory of an Accounts Payable exception desk at an Indian FMCG "
    "company. Extract, for every exception: the vendor, the exception type, the size of the "
    "discrepancy (percent or rupees), the resolution chosen, who decided it, and the business reason "
    "(contract clauses, tolerances, vendor habits, tax rules, fraud signals). Keep amounts, dates, "
    "invoice numbers and contract clause references exactly as written."
)

REFLECT_MISSION = (
    "You are a senior AP controller briefing a colleague. Ground every statement in past decisions, "
    "cite the tolerances and approvers that were actually used, and say clearly when there is not "
    "enough history to be confident."
)

OBSERVATIONS_MISSION = (
    "Consolidate recurring vendor behaviours and the tolerances the AP team applies to them."
)

DIRECTIVES = [
    ("Bank changes need a call-back",
     "Never recommend paying to a changed bank account without a treasury call-back on the phone "
     "number already in the vendor master. Treat every bank-change request as a possible "
     "business-email-compromise attempt.", 100),
    ("Never pay twice",
     "If an invoice looks like a re-send of one already booked, recommend rejecting it as a duplicate.", 90),
    ("Pay only for what was received",
     "Never recommend paying for quantity that has not been received on a GRN.", 80),
]

CALL_TIMEOUT_S = 180

# Hindsight Cloud can keep a document's record after its body is gone (seen after deleting a bank
# and recreating it under the same id). Such a document can never be written again.
BROKEN_DOCUMENT = "no chunks body"


class HindsightError(RuntimeError):
    """A Hindsight call failed; the message says which operation and why."""


def _pointer_file(state_dir: Path, base_bank_id: str) -> Path:
    return Path(state_dir) / f"hindsight-bank-{base_bank_id}.txt"


def resolve_bank_id(base_bank_id: str, state_dir: Path | None) -> str:
    """The bank currently in use for HINDSIGHT_BANK_ID. A rebuild moves to a fresh bank and records
    its id next to the ledger; until the first rebuild it is HINDSIGHT_BANK_ID itself."""
    if state_dir is None:
        return base_bank_id
    p = _pointer_file(state_dir, base_bank_id)
    try:
        active = p.read_text(encoding="utf-8").strip()
    except OSError:
        return base_bank_id
    return active if active.startswith(base_bank_id) else base_bank_id


class _LoopThread:
    """One asyncio event loop running forever on one daemon thread."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name="hindsight-loop", daemon=True)
        self.thread.start()
        self._ready.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._ready.set)
        self.loop.run_forever()

    def run(self, coro, timeout: float = CALL_TIMEOUT_S):
        if threading.current_thread() is self.thread:
            raise RuntimeError("HindsightMemory called from its own loop thread")
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout)
        except FutureTimeout:
            fut.cancel()
            raise

    def stop(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
        if not self.loop.is_running() and not self.loop.is_closed():
            self.loop.close()


class HindsightMemory:
    name = "hindsight"

    def __init__(self, base_url: str, api_key: str | None, bank_id: str, state_dir: Path | None = None):
        """bank_id is HINDSIGHT_BANK_ID. With state_dir set, the bank actually used is the one the
        last rebuild moved to (see reset); without it nothing is written to disk (tests)."""
        try:
            from hindsight_client import Hindsight
        except ImportError as e:  # pragma: no cover - depends on environment
            raise HindsightError(
                "The hindsight-client package is not installed. Run: pip install -r requirements.txt") from e
        if not api_key and "localhost" not in base_url and "127.0.0.1" not in base_url:
            raise HindsightError("HINDSIGHT_API_KEY is empty. Put your key in the .env file.")
        self.base_url, self.base_bank_id, self.state_dir = base_url, bank_id, state_dir
        self.bank_id = resolve_bank_id(bank_id, state_dir)
        self._loop = _LoopThread()
        self.client = self._loop.run(self._make_client(Hindsight, base_url, api_key))
        needed = ("acreate_bank", "alist_directives", "acreate_directive", "adelete_bank", "aretain",
                  "arecall", "areflect", "aclose")
        missing = [n for n in needed if not hasattr(self.client, n)]
        if missing:
            self._loop.stop()
            raise HindsightError(f"hindsight-client is too old (missing {', '.join(missing)}). "
                                 "Run: pip install -U hindsight-client")
        self._closed = False
        atexit.register(self.close)

    @staticmethod
    async def _make_client(cls, base_url, api_key):
        # Built on the loop thread so anything it creates belongs to that loop.
        return cls(base_url=base_url, api_key=api_key, timeout=120.0, user_agent="precedent-ap-agent/2.0")

    def _call(self, what: str, coro):
        try:
            return self._loop.run(coro)
        except FutureTimeout as e:
            raise HindsightError(f"Hindsight {what} timed out after {CALL_TIMEOUT_S}s") from e
        except HindsightError:
            raise
        except Exception as e:
            raise HindsightError(f"Hindsight {what} failed: {_describe(e)}") from e

    def close(self) -> None:
        """Close the HTTP session and stop the loop (no 'Unclosed client session' warnings)."""
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.run(self.client.aclose(), timeout=10)
        except Exception as e:  # best effort at shutdown
            log.debug("close: %s", e)
        self._loop.stop()

    # ---- bank lifecycle -------------------------------------------------
    def setup(self) -> None:
        self._call("create_bank", self.client.acreate_bank(
            self.bank_id,
            retain_mission=RETAIN_MISSION,
            reflect_mission=REFLECT_MISSION,
            enable_observations=True,
            observations_mission=OBSERVATIONS_MISSION,
        ))
        try:
            listing = self._call("list_directives", self.client.alist_directives(self.bank_id))
            existing = {d.name for d in (getattr(listing, "items", None) or [])}
        except HindsightError as e:
            log.info("could not list directives (%s); will try to create them", e)
            existing = set()
        for name, content, prio in DIRECTIVES:
            if name in existing:
                continue
            try:
                self._call("create_directive",
                           self.client.acreate_directive(self.bank_id, name=name, content=content, priority=prio))
            except HindsightError as e:
                log.warning("directive %r not created: %s", name, e)

    def reset(self) -> None:
        """Start over in a brand-new, empty bank and delete the old one.

        Never recreates a bank under the same id: on Hindsight Cloud that left records of the old
        documents whose bodies were gone, and every retain of those document ids then failed with
        HTTP 500 "no chunks body ... no retry will help". Raises if the new bank can't be created,
        so callers never clear their own records while still pointing at the old history."""
        old = self.bank_id
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        new = f"{self.base_bank_id}-r{stamp}"
        if new == old:  # two resets within one second
            new = f"{new}{uuid.uuid4().hex[:4]}"
        self.bank_id = new
        try:
            self.setup()
        except Exception:
            self.bank_id = old
            raise
        if self.state_dir is not None:
            p = _pointer_file(self.state_dir, self.base_bank_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(new + "\n", encoding="utf-8")
        try:  # best effort: the old bank is no longer used either way
            self._call("delete_bank", self.client.adelete_bank(old))
        except HindsightError as e:
            log.warning("old bank %s not deleted: %s", old, e)

    def ping(self) -> dict:
        """Cheap connectivity check used by `python -m app.doctor`."""
        r = self._call("recall", self.client.arecall(bank_id=self.bank_id, query="ping", max_tokens=256,
                                                     budget="low"))
        return {"ok": True, "results": len(r.results or [])}

    # ---- core operations ------------------------------------------------
    def retain(self, content, *, context, timestamp=None, tags=None, metadata=None, document_id=None):
        """Returns the document id actually used (it differs only if Hindsight had broken the
        original one; see BROKEN_DOCUMENT)."""
        def call(doc_id):
            self._call("retain", self.client.aretain(
                bank_id=self.bank_id,
                content=content,
                context=context,
                timestamp=timestamp,
                tags=tags,
                metadata={k: str(v) for k, v in (metadata or {}).items()},
                document_id=doc_id,
            ))
        try:
            call(document_id)
            return document_id
        except HindsightError as e:
            if not document_id or BROKEN_DOCUMENT not in str(e):
                raise
            # That document id is unusable on the server; store the same memory under a fresh id.
            fresh = f"{re.sub(r'~[0-9a-f]{6}$', '', document_id)}~{uuid.uuid4().hex[:6]}"
            log.warning("Hindsight document %s is broken on the server; retaining as %s", document_id, fresh)
            call(fresh)
            return fresh

    def recall(self, query, *, tags=None, tags_match="any", limit=12):
        resp = self._call("recall", self.client.arecall(
            bank_id=self.bank_id,
            query=query,
            types=["world", "experience", "observation"],
            budget="mid",
            max_tokens=4096,
            tags=tags,
            tags_match=tags_match,
        ))
        items, seen_docs = [], set()
        for r in resp.results or []:
            # Hindsight extracts several facts per retained document; keep one row per case so a
            # case doesn't vote several times. Observations have no document and are kept.
            key = r.document_id or r.id
            if key in seen_docs:
                continue
            seen_docs.add(key)
            scores = getattr(r, "scores", None)
            items.append(MemoryItem(id=r.id, text=r.text, tags=list(r.tags or []),
                                    metadata=dict(r.metadata or {}),
                                    timestamp=r.occurred_start or r.mentioned_at,
                                    document_id=r.document_id,
                                    score=getattr(scores, "final", None) if scores is not None else None))
            if len(items) >= limit:
                break
        return items

    def reflect(self, query, *, tags=None, response_schema=None):
        resp = self._call("reflect", self.client.areflect(
            bank_id=self.bank_id,
            query=query,
            budget="mid",
            tags=tags,
            tags_match="any_strict" if tags else "any",
            response_schema=response_schema,
            include_facts=True,
        ))
        based_on = []
        bo = getattr(resp, "based_on", None)
        for f in (getattr(bo, "memories", None) or []) if bo is not None else []:
            based_on.append(getattr(f, "text", str(f)))
        return Reflection(text=resp.text, structured=resp.structured_output, based_on=based_on)


def _describe(e: Exception) -> str:
    status = getattr(e, "status", None)
    reason = getattr(e, "reason", None) or ""
    body = str(getattr(e, "body", "") or "")[:300]
    if status == 401 or status == 403:
        return f"HTTP {status} — check HINDSIGHT_API_KEY in .env"
    if status:
        return f"HTTP {status} {reason} {body}".strip()
    return f"{type(e).__name__}: {e}"
