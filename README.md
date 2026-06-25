# RAG Sync

RAG Sync is a local ingestion control layer for syncing Atlas source files into RAGFlow.

It handles three things:
- scanning Atlas source folders
- converting files into clean Markdown artifacts
- uploading and indexing those artifacts in RAGFlow

Mental model:

```text
Atlas folders = source files and source of truth
rag-sync = generated upload/indexing workspace and machine registry
RAGFlow = indexed retrieval database
```

The app keeps generated files under `data/`, tracks state in SQLite, and never modifies source files.

## Features

- profile-based source scanning
- Marker, MinerU, and passthrough parsing
- queue-based convert/upload/parse workflow
- web UI for files, jobs, settings, and retrieval test wiring
- pause and hard-stop controls for long overnight runs

## Development

```bash
uv sync --dev
uv run pytest
uv run rag-sync --help
```

Frontend:

```bash
cd web
npm install
npm run build
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
