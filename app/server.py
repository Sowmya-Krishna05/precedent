"""HTTP API + web UI.

    python -m app.server          # http://localhost:8000
"""
from __future__ import annotations

import logging
import os
import threading

os.environ.setdefault("FLASK_SKIP_DOTENV", "1")  # .env is loaded by app/config.py

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

from .config import ROOT, VERSION, settings  # noqa: E402
from .service import PrecedentService  # noqa: E402

log = logging.getLogger("precedent")

RESET_CONFIRM = "RESET"


def create_app(svc: PrecedentService | None = None, setup_error: str | None = None) -> Flask:
    """Build the Flask app around a PrecedentService (tests pass their own)."""
    app = Flask(__name__, static_folder=str(ROOT / "app" / "static"), static_url_path="/static")
    state = {"svc": svc, "setup_error": setup_error}

    def S() -> PrecedentService:
        if state["svc"] is None:
            raise RuntimeError(state["setup_error"] or "Service not initialised")
        return state["svc"]

    def err(e: Exception | str, code: int = 500):
        if isinstance(e, Exception):
            log.exception("request failed")
            msg = str(e) if type(e).__name__ in ("HindsightError", "RuntimeError") else f"{type(e).__name__}: {e}"
        else:
            msg = e
        return jsonify({"error": msg}), code

    def busy():
        if state["svc"] is None:
            return err(state["setup_error"] or "Service not initialised", 503)
        if S().progress.get("running"):
            return jsonify({"error": "Rebuilding memory from history. The inbox opens when it finishes.",
                            "busy": True}), 409
        return None

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/status")
    def status():
        if state["svc"] is None:
            return jsonify({"version": VERSION, "setup_error": state["setup_error"], "resolved": 0,
                            "progress": {"running": False}, "memory_backend": settings.memory_backend,
                            "bank_id": settings.bank_id, "llm": None,
                            "company": {"name": "Precedent", "city": ""}})
        return jsonify({**S().status(), "setup_error": state["setup_error"]})

    @app.post("/api/replay")
    def replay():
        """Wipes the memory bank and replays 11 weeks of history. Requires {"confirm": "RESET"}."""
        if (b := busy()):
            return b
        body = request.get_json(silent=True) or {}
        if body.get("confirm") != RESET_CONFIRM:
            return err('This deletes the memory bank. Send {"confirm": "RESET"} to proceed.', 400)
        svc_ = S()
        svc_.progress.update(running=True, done=0, total=0, message="Resetting memory bank")

        def run():
            try:
                svc_.reset()
                svc_.replay()
                svc_.progress["message"] = "Memory rebuilt from 11 weeks of history"
            except Exception as e:
                log.exception("replay failed")
                svc_.progress.update(message=f"Rebuild failed: {e}")
            finally:
                svc_.progress["running"] = False

        threading.Thread(target=run, daemon=True, name="replay").start()
        return jsonify({"started": True})

    @app.get("/api/metrics")
    def metrics():
        if state["svc"] is None:
            return jsonify({"weeks": [], "totals": {}})
        return jsonify(S().metrics())

    @app.get("/api/inbox")
    def inbox():
        if (b := busy()):
            return b
        try:
            return jsonify(S().inbox())
        except Exception as e:
            return err(e)

    @app.get("/api/invoice/<invoice_id>")
    def invoice(invoice_id):
        if (b := busy()):
            return b
        if invoice_id not in S().store.invoice_by_id:
            return err("Invoice not found", 404)
        try:
            return jsonify(S().detail(invoice_id))
        except Exception as e:
            return err(e)

    @app.get("/api/invoice/<invoice_id>/baseline")
    def baseline(invoice_id):
        """Same invoice, same LLM, memory OFF: the 'before' half of the before/after."""
        if (b := busy()):
            return b
        if invoice_id not in S().store.invoice_by_id:
            return err("Invoice not found", 404)
        try:
            _, _, d = S().analyze(invoice_id, memory=False)
            return jsonify(d.to_dict())
        except Exception as e:
            return err(e)

    @app.post("/api/invoice/<invoice_id>/resolve")
    def resolve(invoice_id):
        if (b := busy()):
            return b
        body = request.get_json(silent=True) or {}
        if invoice_id not in S().store.invoice_by_id:
            return err("Invoice not found", 404)
        if "action" not in body:
            return err("Missing 'action'", 400)
        try:
            case = S().resolve(invoice_id, body["action"], body.get("decided_by") or "AP reviewer",
                               (body.get("reason") or "").strip() or "Accepted the agent's recommendation.")
            return jsonify(case)
        except ValueError as e:
            return err(str(e), 400)
        except Exception as e:
            return err(e)

    @app.get("/api/vendors")
    def vendors():
        if state["svc"] is None:
            return jsonify([])
        counts: dict[str, int] = {}
        for c in S().ledger.values():
            if c["exception_type"]:
                counts[c["vendor_id"]] = counts.get(c["vendor_id"], 0) + 1
        return jsonify([{**v, "cases": counts.get(v["id"], 0)} for v in S().store.vendors.values()])

    @app.get("/api/vendor/<vendor_id>/playbook")
    def playbook(vendor_id):
        if (b := busy()):
            return b
        if vendor_id not in S().store.vendors:
            return err("Vendor not found", 404)
        try:
            return jsonify(S().agent.vendor_playbook(vendor_id))
        except Exception as e:
            return err(e)

    @app.get("/api/cases")
    def cases():
        if state["svc"] is None:
            return jsonify([])
        rows = sorted((c for c in S().ledger.values() if c["exception_type"]),
                      key=lambda c: (c["received_at"], c["invoice_id"]), reverse=True)
        return jsonify(rows[:200])

    return app


def build() -> tuple[Flask, PrecedentService | None]:
    try:
        svc = PrecedentService(settings)
        svc.setup()
        return create_app(svc), svc
    except Exception as e:
        log.error("Startup problem: %s", e)
        return create_app(None, f"{e}. Run: python -m app.doctor"), None


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app, svc = build()
    mem = f"{svc.memory.name} ({getattr(svc.memory, 'bank_id', '')})" if svc else "NOT CONNECTED"
    print(f"\n  Precedent v{VERSION}\n  Memory: {mem}\n  LLM:    {svc.llm.model if svc and svc.llm else 'off'}\n"
          f"  Cases in memory: {len(svc.ledger) if svc else 0}"
          f"{'   (run: python scripts/replay.py)' if svc and not svc.ledger else ''}\n"
          f"  Open:   http://localhost:{settings.port}\n")
    app.run(host="127.0.0.1", port=settings.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
