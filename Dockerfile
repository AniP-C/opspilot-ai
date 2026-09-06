# Single-container image for hosting (Hugging Face Spaces / Render / any Docker host).
# Runs BOTH the FastAPI API (internal :8000) and the Streamlit console (public :$PORT).
# Build context = repo root:  docker build -f deploy/Dockerfile -t opspilot-web .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# libgomp1: onnxruntime (chromadb dep); libpq5: psycopg2 (unused in SQLite demo, kept for parity).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY ui ./ui
COPY scripts ./scripts
COPY ops-pilot-demo-data ./ops-pilot-demo-data
COPY deploy/start_all.sh ./deploy/start_all.sh
RUN chmod +x ./deploy/start_all.sh

# Safe-by-default demo configuration (all overridable, but keep mock in public).
ENV PORT=7860 \
    API_BASE_URL=http://localhost:8000 \
    EXECUTION_MODE=mock \
    ALLOW_REAL_EXECUTION=false \
    SERVICENOW_MODE=mock \
    NEWRELIC_MODE=mock

EXPOSE 7860
CMD ["./deploy/start_all.sh"]
