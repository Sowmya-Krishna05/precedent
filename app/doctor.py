"""Check that everything Precedent needs is in place, and say exactly what to fix.

    python -m app.doctor
"""
from __future__ import annotations

import importlib
import sys
import threading
import time

OK, WARN, FAIL = "  OK  ", " WARN ", " FAIL "
results: list[tuple[str, str, str]] = []
for _s in (sys.stdout, sys.stderr):  # Windows consoles: never crash on ₹ / ✓
    try:
        _s.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass


def report(status: str, what: str, detail: str = "") -> None:
    results.append((status, what, detail))
    print(f"[{status}] {what}" + (f"\n         {detail}" if detail else ""), flush=True)


def main() -> int:
    from app.config import ENV_FILE, PLACEHOLDERS, ROOT, VERSION, read_dotenv
    print(f"Precedent v{VERSION} doctor\nProject folder: {ROOT}\n")

    # 1. Python
    v = sys.version_info
    if v >= (3, 10):
        report(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        report(FAIL, f"Python {v.major}.{v.minor}", "Precedent needs Python 3.10 or newer.")

    # 2. Packages
    missing = []
    for mod, pkg in (("flask", "flask"), ("requests", "requests"), ("hindsight_client", "hindsight-client")):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        report(FAIL, "Python packages", f"Missing {', '.join(missing)}. Activate the venv, then: "
                                         "pip install -r requirements.txt")
    else:
        report(OK, "Python packages (flask, requests, hindsight-client)")

    # 3. .env
    env = read_dotenv(ENV_FILE)
    if not ENV_FILE.exists():
        report(FAIL, ".env file", f"Not found at {ENV_FILE}. Copy .env.example to .env and add your keys.")
    else:
        def real(k):
            val = env.get(k, "")
            return bool(val) and not any(val.startswith(p) for p in PLACEHOLDERS)
        backend = env.get("MEMORY_BACKEND", "hindsight").lower()
        report(OK, f".env found (MEMORY_BACKEND={backend})")
        if backend == "hindsight" and not real("HINDSIGHT_API_KEY") and "localhost" not in env.get("HINDSIGHT_API_URL", ""):
            report(FAIL, "HINDSIGHT_API_KEY", "Empty or still the placeholder. Paste your key from "
                                              "ui.hindsight.vectorize.io into .env.")
        elif backend == "hindsight":
            report(OK, "HINDSIGHT_API_KEY is set")
        if real("GROQ_API_KEY"):
            report(OK, "GROQ_API_KEY is set")
        else:
            report(WARN, "GROQ_API_KEY", "Not set: the app works, but decisions use memory evidence only "
                                          "(no LLM explanations).")

    if any(r[0] == FAIL for r in results):
        return summary()

    from app.config import settings  # noqa: E402  (after .env is known to be sane)

    # 4. Hindsight
    if settings.memory_backend == "hindsight":
        from app.config import RUNTIME_DIR
        from app.memory.hindsight_backend import HindsightMemory
        try:
            t0 = time.time()
            m = HindsightMemory(settings.hindsight_url, settings.hindsight_api_key, settings.bank_id,
                                state_dir=RUNTIME_DIR)
            active_bank = m.bank_id
            m.setup()
            m.ping()
            report(OK, f"Hindsight reachable at {settings.hindsight_url} "
                       f"(bank {active_bank}, {time.time() - t0:.1f}s)")
            errors: list[str] = []

            def worker():
                try:
                    m.recall("doctor check", tags=["kind:case"], tags_match="any_strict", limit=1)
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e))
            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            if errors:
                report(FAIL, "Hindsight from several threads (what the web server does)", errors[0])
            else:
                report(OK, "Hindsight from several threads at once (what the web server does)")
            n = len(m.recall("AP exception resolved", tags=["kind:case"], tags_match="any_strict", limit=50))
            m.close()
        except Exception as e:
            report(FAIL, "Hindsight connection", str(e))
            return summary()
    else:
        report(WARN, f"MEMORY_BACKEND={settings.memory_backend}",
               "Offline stand-in, not Hindsight. Use MEMORY_BACKEND=hindsight for the real demo.")
        n = None

    # 5. Groq
    if settings.llm_api_key:
        from app.llm import ChatClient, LLMError
        try:
            c = ChatClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model, max_retries=1, timeout=30)
            c.chat([{"role": "user", "content": "Reply with the word OK."}])
            report(OK, f"Groq reachable ({settings.llm_model})")
        except LLMError as e:
            msg = str(e)
            hint = " Check GROQ_API_KEY in .env." if "401" in msg else ""
            report(FAIL, "Groq", msg[:200] + hint)

    # 6. History
    import json

    from app.config import RUNTIME_DIR  # noqa: E402
    from app.memory.hindsight_backend import resolve_bank_id  # noqa: E402
    from app.service import ledger_file  # noqa: E402
    path = ledger_file(settings.memory_backend,
                       resolve_bank_id(settings.bank_id, RUNTIME_DIR)
                       if settings.memory_backend == "hindsight" else None)
    ledger = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if ledger:
        report(OK, f"History loaded: {len(ledger)} invoices in the case list"
                   + (f", {n}+ cases found in Hindsight" if n is not None else ""))
        if n == 0:
            report(WARN, "Case list and bank disagree", "The bank has no cases. Run: python scripts/replay.py")
    else:
        report(WARN, "No history yet", "Run: python scripts/replay.py   (about 4 minutes)")

    return summary()


def summary() -> int:
    fails = [r for r in results if r[0] == FAIL]
    print()
    if fails:
        print(f"{len(fails)} problem(s) to fix first. The first one: {fails[0][1]}")
        return 1
    print("All set. Start the app with: python -m app.server")
    return 0


if __name__ == "__main__":
    sys.exit(main())
