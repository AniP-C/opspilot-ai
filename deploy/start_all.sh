#!/usr/bin/env bash
# Single-container boot for hosting: run the FastAPI API (internal :8000) AND the
# Streamlit console (public :$PORT) in one container. Safe by default — mock
# everything; real execution is impossible. State (SQLite + Chroma) is rebuilt at
# boot, so ephemeral hosting disks are fine.
set -euo pipefail

PORT="${PORT:-7860}"                          # HF Spaces=7860; Render sets $PORT
export API_BASE_URL="http://localhost:8000"   # UI -> API, server-side
# Write DB + vector store to a guaranteed-writable path (hosts often run the app
# dir read-only / as a non-root user). Overridable, but /tmp is a safe default.
export DATABASE_URL="${DATABASE_URL:-sqlite:////tmp/opspilot.db}"
export CHROMA_PATH="${CHROMA_PATH:-/tmp/opspilot_chroma}"
# Hard safety defaults for a public demo (never enable real mutation):
export EXECUTION_MODE="mock"
export ALLOW_REAL_EXECUTION="false"
export SERVICENOW_MODE="${SERVICENOW_MODE:-mock}"
export NEWRELIC_MODE="${NEWRELIC_MODE:-mock}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "[start] seeding operational data (SQLite) ..."
python -m scripts.seed_db          # creates tables + loads the synthetic dataset
echo "[start] ingesting knowledge base into Chroma ..."
python -m scripts.ingest_knowledge

echo "[start] launching API on :8000 (internal) ..."
uvicorn app.main:app --host 0.0.0.0 --port 8000 &

echo "[start] waiting for API health ..."
for _ in $(seq 1 60); do
  if python - <<'PY' 2>/dev/null
import sys, httpx
sys.exit(0 if httpx.get("http://localhost:8000/health", timeout=2).status_code == 200 else 1)
PY
  then echo "[start] API healthy"; break; fi
  sleep 1
done

echo "[start] launching Streamlit console on :$PORT (public) ..."
exec streamlit run ui/streamlit_app.py \
  --server.address=0.0.0.0 --server.port="$PORT" \
  --server.headless=true --server.enableCORS=false --server.enableXsrfProtection=false
