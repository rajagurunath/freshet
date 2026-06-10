# Context Hub — Central API

FastAPI service that ingests AI coding-assistant sessions, stores them in S3 (or local disk), indexes them in LanceDB, and powers a company-wide RAG agent backed by Claude.

## Quick start

```bash
cd apps/api

# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install (editable, with dev deps)
pip install -e ".[dev]"

# 3. Copy and edit env
cp .env.example .env
# Set ANTHROPIC_API_KEY if you want real summaries + RAG answers.
# Leave blank to run in stub mode (server fully functional, no external creds).

# 4. Run
python -m contexthub.main
# → http://localhost:8787
# → http://localhost:8787/docs  (Swagger UI)
```

## Running tests

```bash
pytest -q
```

Tests use `EMBEDDING_PROVIDER=hash` (fully offline, no model download) and
temporary directories, so they pass without any external credentials.

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/healthz` | None | Liveness probe |
| POST | `/v1/sessions` | Bearer | Ingest a session envelope |
| GET | `/v1/sessions` | Bearer | List catalog (filterable) |
| GET | `/v1/sessions/{id}` | Bearer | Fetch catalog row + raw blob |
| POST | `/v1/summarize` | Bearer | Summarize a session |
| POST | `/v1/query` | Bearer | RAG query across all sessions |
| GET | `/v1/stats` | Bearer | Aggregate stats |

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | _(none)_ | Enables real Claude summaries + RAG. Omit for stub mode. |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Claude model to use. |
| `EMBEDDING_PROVIDER` | `local` | `local` (sentence-transformers) or `hash` (offline CI). |
| `S3_BUCKET` | _(none)_ | S3 bucket for raw blobs. Omit for local-file fallback. |
| `AWS_REGION` | `us-east-1` | AWS region for S3. |
| `LANCEDB_URI` | `./data/lancedb` | LanceDB URI (local path or `s3://`). |
| `BLOB_DIR` | `./data/blobs` | Local blob store directory (used when `S3_BUCKET` not set). |
| `API_KEYS` | `dev-key` | Comma-separated list of valid bearer tokens. |
| `CORS_ORIGINS` | _(Tauri + Vite dev)_ | Comma-separated CORS allowed origins. |

## curl examples

```bash
# Health check
curl http://localhost:8787/healthz

# Ingest a session
curl -X POST http://localhost:8787/v1/sessions \
  -H "Authorization: Bearer dev-key" \
  -H "Content-Type: application/json" \
  -d @examples/session_envelope.json

# List sessions
curl http://localhost:8787/v1/sessions \
  -H "Authorization: Bearer dev-key"

# Filter by category
curl "http://localhost:8787/v1/sessions?category=engineering" \
  -H "Authorization: Bearer dev-key"

# RAG query
curl -X POST http://localhost:8787/v1/query \
  -H "Authorization: Bearer dev-key" \
  -H "Content-Type: application/json" \
  -d '{"question": "How did we handle S3 retry logic?", "top_k": 5}'

# Stats
curl http://localhost:8787/v1/stats \
  -H "Authorization: Bearer dev-key"
```

## Docker

```bash
docker build -t contexthub-api .
docker run -p 8787:8787 \
  -e ANTHROPIC_API_KEY=sk-... \
  -e API_KEYS=my-secure-key \
  -v $(pwd)/data:/app/data \
  contexthub-api
```
