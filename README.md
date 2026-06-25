# RAG Sync

RAG Sync is a local ingestion control layer for syncing Atlas source files into RAGFlow.

Mental model:

```text
Atlas folders = source files and source of truth
rag-sync = generated upload/indexing workspace and machine registry
RAGFlow = indexed retrieval database
```

The app keeps generated files under `data/`, tracks state in SQLite, and never modifies source files.

## Development

```bash
uv sync --dev
uv run pytest
uv run rag-sync --help
```

## Running Locally

Backend:

```bash
cd /home/saulius/atlas-services/rag-sync
uv run uvicorn rag_sync.api:app --host 0.0.0.0 --port 8091
```

Frontend:

```bash
cd /home/saulius/atlas-services/rag-sync/web
npm run dev -- --host 0.0.0.0 --port 5174
```

Tailscale URLs on the Linux PC:

```text
Backend: http://100.87.230.80:8091/api/health
Frontend: http://100.87.230.80:5174
```

The app reads the RAGFlow API key from:

```text
/home/saulius/atlas-services/ragflow/source/docker/.env
```

It does not store API keys in SQLite.
