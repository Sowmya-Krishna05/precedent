#!/usr/bin/env bash
# Precedent v2 setup for macOS / Linux:  bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"
echo "=== Precedent v2 setup ===  ($(pwd))"

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || { echo "python3 not found. Install Python 3.10+."; exit 1; }
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env — add HINDSIGHT_API_KEY and GROQ_API_KEY, then re-run: bash setup.sh"
  exit 0
fi

.venv/bin/python -m app.doctor || true
cat <<'EOF'

Next steps:
  source .venv/bin/activate
  python scripts/replay.py      # first time only, ~4 minutes
  python -m app.server          # then open http://localhost:8000
EOF
