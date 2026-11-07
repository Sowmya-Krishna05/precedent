# Setup from zero (Windows)

Use a **new, empty folder** for v2. Don't extract it over the old `precedent` folder.

## 1. Unzip

1. Right-click `precedent-v2.zip` → **Extract All…** → choose `C:\Users\<you>\Projects` (or anywhere).
2. You now have `C:\Users\<you>\Projects\precedent-v2\` containing `setup.bat`, `app\`, `data\`, …
   If you see `precedent-v2\precedent-v2\`, use the inner one.

## 2. Run setup (one double-click)

Double-click **`setup.bat`**. It:

- creates a private Python environment in `.venv`
- installs the packages
- creates `.env` and opens it in Notepad
- runs the checker (`python -m app.doctor`)

In Notepad, replace the two placeholder values, then **save and close**:

```
HINDSIGHT_API_KEY=hsk_...your real key...
GROQ_API_KEY=gsk_...your real key...
```

Get the keys from:
- Hindsight: https://ui.hindsight.vectorize.io → API keys. Under Billing, apply promo code **MEMHACK99**.
- Groq: https://console.groq.com/keys

The checker should end with **"All set"**. If it shows `[ FAIL ]`, its message says what to fix.
Run `setup.bat` again after fixing.

## 3. Load the history (first time only, about 4 minutes)

Open PowerShell **in the precedent-v2 folder**. In File Explorer, click the address bar, type
`powershell`, press Enter. Then:

```powershell
.venv\Scripts\activate
python scripts\replay.py
```

It ends with the learning-curve table: about **88% auto-cleared, 100% matched** in the last 4 weeks.

## 4. Start the app

```powershell
python -m app.server
```

The window prints:

```
  Precedent v2.0
  Memory: hindsight (precedent-deccan-fresh-ap)
  LLM:    openai/gpt-oss-120b
  Cases in memory: 96
  Open:   http://localhost:8000
```

Open http://localhost:8000. Top right of the page shows the bank name and **v2.0**.

## Every time after that

```powershell
cd C:\Users\<you>\Projects\precedent-v2
.venv\Scripts\activate
python -m app.server
```

## If something's wrong

| You see | Do this |
|---|---|
| Anything unexpected | `python -m app.doctor`; it checks Python, packages, `.env`, Hindsight, Groq and the history, and names the fix |
| Top right doesn't say **v2.0** | You're running old files. `cd` into the `precedent-v2` folder |
| "No history yet" | `python scripts\replay.py` |
| Replay stopped part-way | `python scripts\replay.py --keep` resumes it |
| `running scripts is disabled` when activating | Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, answer `Y` |
| Port 8000 busy | Set `PORT=8001` in `.env` |

## Rehearse without spending Hindsight credits (optional)

A local Hindsight-compatible test server ships in `tests\`. It's for rehearsal only; record the
demo on real Hindsight.

```powershell
python -m tests.fake_hindsight_server --port 8888     # window 1
```

In `.env` set `HINDSIGHT_API_URL=http://localhost:8888`, then in window 2 run the replay and server
as above. Switch the URL back to `https://api.hindsight.vectorize.io` afterwards.
