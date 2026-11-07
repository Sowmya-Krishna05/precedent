# What changed in v2

v1's memory logic was proven on real Hindsight (88% auto-cleared, 100% precision). v2 rebuilds
everything around it that failed on a real Windows machine.

| v1 problem | v2 |
|---|---|
| Web server crashed on every Hindsight call: `Timeout context manager should be used inside a task` | The SDK runs on one dedicated event loop; any thread can call it. Tested with the real SDK + aiohttp 3.13 from 8 threads at once, and a test that reproduces the original crash |
| "Unclosed client session" after replay | Session and loop closed cleanly on exit |
| A stray `MEMORY_BACKEND=local` in the terminal silently overrode `.env` | `.env` is the source of truth |
| "Replay" button deleted the bank; when Hindsight failed mid-way, the case list was wiped anyway | Button needs explicit confirmation; a failed reset leaves everything untouched |
| Hard to tell which version was running | Version shown at startup and in the page header |
| Inbox waited on the LLM for every invoice | Inbox uses memory evidence (fast); the LLM runs when you open an invoice |
| Windows text-encoding garbling (`â€¢`) | All files read and written as UTF-8 |
| Setup was manual and error-prone | `setup.bat` / `setup.sh`, and `python -m app.doctor` checks everything |
| Startup errors crashed the server | The page shows the problem and the fix |
