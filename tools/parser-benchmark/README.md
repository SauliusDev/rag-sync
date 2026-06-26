# Marker Parser Benchmark

This benchmark extracts a fixed page slice from a source PDF, runs Marker with the same non-LLM flags used by `rag-sync`, and writes persistent logs and summaries.

## Defaults

- Source PDF: `Matrix Cookbook - Kaare Brandt Petersen & Michael Syskind Pedersen.pdf`
- Page range: `1-10`
- Marker flags:
  - `--output_format markdown`
  - `--disable_ocr`
  - `--disable_image_extraction`
  - `--workers 1`

## System prerequisites

Ubuntu/Debian example:

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv
```

Cluster notes:

- CUDA drivers must already be installed by the cluster image or admin.
- Use the same Marker version on both machines for fair comparisons.
- Use the same sample page range for fair comparisons.
- This benchmark does not use Marker `--use_llm`.

## Python environment

From this repo:

```bash
cd /path/to/rag-sync
uv sync --dev
```

Standalone virtualenv option:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install pypdf marker-pdf
```

## Run

```bash
python tools/parser-benchmark/benchmark_marker.py
python tools/parser-benchmark/benchmark_marker.py --page-start 21 --page-count 10
python tools/parser-benchmark/benchmark_marker.py --marker-bin /path/to/marker
```

## Outputs

- `tools/parser-benchmark/logs/marker-benchmark.jsonl`
- `tools/parser-benchmark/artifacts/samples/`
- `tools/parser-benchmark/artifacts/runs/<timestamp>/summary.json`
- `tools/parser-benchmark/artifacts/runs/<timestamp>/summary.md`

## Comparison rules

Speed comparisons are only meaningful when all of these stay fixed:

- same PDF slice
- same Marker version
- same Marker flags
- same worker count

## Notes for copying to another machine

If you copy this repo to the KTU cluster:

1. install Python and Marker there
2. confirm the source PDF path exists or pass `--source-pdf`
3. point `--marker-bin` to the cluster Marker executable if needed
4. keep the same page range when comparing machines
