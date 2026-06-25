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
