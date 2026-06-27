# Marker Cluster UI

Standalone terminal runner for cluster-side Marker batch conversion. It wraps `rag_sync.marker_batch.run_batch(...)` and prints a compact Rich summary with the batch id, counts, manifest path, and log path.

## Prerequisites

- Python 3.12
- `uv`
- a working `marker` executable on `PATH`, or pass `--marker-bin`
- the same Python environment needs `rag-sync` and `rich` available
- if your Marker environment is `marker-pdf==1.10.2`, make sure `psutil` is installed there as well

## Usage

Run the standalone tool from the repository root:

```bash
uv run python tools/marker_cluster_ui/run.py \
  --input-dir /path/to/pdfs \
  --output-dir /path/to/batch \
  --profile quant-books
```

Add tags or override the Marker binary when needed:

```bash
uv run python tools/marker_cluster_ui/run.py \
  --input-dir /cluster/pdfs \
  --output-dir /cluster/batches/2026-06-27-quant-books \
  --profile quant-books \
  --tag gpu \
  --tag nightly \
  --marker-bin /opt/marker/bin/marker
```

## Output

The runner writes batch artifacts under `--output-dir` using the existing batch service:

- `manifest.json`
- `logs/run.jsonl`
- generated Markdown files under `outputs/`

Exit code is `0` when all files convert successfully, otherwise `1`.
