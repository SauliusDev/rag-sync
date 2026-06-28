# Parser Benchmark

This benchmark extracts a fixed page slice from a source PDF, runs multiple parsers against the exact same sample, and writes durable logs, heartbeat snapshots, and per-parser summaries.

It currently supports:

- `marker`
- `mineru`
- `paddleocr_vl`
- `glmocr`

## Why this shape

The point is not just total wall time. The tool also captures:

- per-parser command and version
- append-only JSONL logs
- GPU snapshots during execution
- output file growth while the parser is still running
- markdown/json file counts and bytes
- pages per minute on the sampled slice

That gives you something better than running blind when a parse takes a long time.

## What each parser exposes

- `Marker`: local CLI only. No native percent-complete signal in the benchmark path, so the tool logs its own heartbeat and GPU snapshots.
- `MinerU`: local CLI plus async task APIs in the official docs. The benchmark currently runs the local CLI, but the README keeps the API note because MinerU is the one parser here that explicitly documents task submission, status lookup, and result retrieval.
- `PaddleOCR-VL`: local CLI and Python API, plus service deployment and VLM-service modes. No official page-level percent signal surfaced here, so the benchmark uses output-file growth and GPU heartbeats.
- `GLM-OCR`: CLI, Python API, MaaS mode, self-hosted vLLM/SGLang mode, and a small Flask server. No official percent signal surfaced here either.

## System prerequisites

Ubuntu/Debian example:

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv
```

Cluster notes:

- CUDA drivers must already be installed by the cluster image or admin.
- This machine already has `2x NVIDIA H100 NVL`, which is enough for the local setups this folder targets.
- Keep the same PDF slice when comparing parsers or machines.

## Clean setup

The benchmark expects isolated parser envs under `tools/parser-benchmark/.venvs/` so their dependencies do not collide.

Install everything:

```bash
bash tools/parser-benchmark/setup_parsers.sh all
```

Install only one parser:

```bash
bash tools/parser-benchmark/setup_parsers.sh mineru
bash tools/parser-benchmark/setup_parsers.sh paddleocr
bash tools/parser-benchmark/setup_parsers.sh glmocr
```

What the setup script does:

- `marker`: installs `marker-pdf`
- `mineru`: installs `mineru[all]`
- `paddleocr`: installs `paddlepaddle-gpu==3.2.1` for `cu126` plus `paddleocr[doc-parser]`
- `glmocr`: installs `glmocr[selfhosted]` plus `vllm` and `transformers`

## Config

Default config lives at `tools/parser-benchmark/benchmark.toml`.

Important defaults:

- `mineru` uses `backend = "hybrid-engine"` with `effort = "high"` to target the stronger MinerU2.5-Pro style path rather than the lightweight `pipeline` baseline.
- `paddleocr_vl` uses `pipeline_version = "v1.5"` by default.
- `glmocr` is configured for local CLI use; if you want MaaS or a self-hosted API/server config, point `config_path` at your own `glmocr` YAML config.

## Run

List parser ids:

```bash
python tools/parser-benchmark/benchmark_parsers.py --list-parsers
```

Run the full suite:

```bash
python tools/parser-benchmark/benchmark_parsers.py
```

Run only the new top-grade candidates:

```bash
python tools/parser-benchmark/benchmark_parsers.py --parsers mineru,paddleocr_vl,glmocr
```

Use a different slice:

```bash
python tools/parser-benchmark/benchmark_parsers.py --page-start 21 --page-count 10
```

## Outputs

- `tools/parser-benchmark/logs/parser-benchmark.jsonl`
- `tools/parser-benchmark/artifacts/samples/`
- `tools/parser-benchmark/artifacts/runs/<timestamp>/environment.json`
- `tools/parser-benchmark/artifacts/runs/<timestamp>/summary.json`
- `tools/parser-benchmark/artifacts/runs/<timestamp>/summary.md`

During each parser run, the JSONL log includes `parser.progress` events with:

- elapsed seconds
- GPU utilization and memory snapshots from `nvidia-smi`
- markdown/json file counts and bytes so far
- an estimated percent when the parser emits one markdown file per page, which is most useful for `PaddleOCR-VL` on PDF slices

## Official install notes used here

- `MinerU`: `uv pip install -U "mineru[all]"`, CLI/API/router support, async task endpoint `POST /tasks`, local orchestration client behavior.
- `PaddleOCR-VL`: official Docker image or `python -m pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/` plus `python -m pip install -U "paddleocr[doc-parser]"`.
- `GLM-OCR`: `pip install "glmocr[selfhosted]"`, then `vllm serve zai-org/GLM-OCR ...` for self-hosted OCR serving, or plain `pip install glmocr` for MaaS mode.

## Comparison rules

Speed comparisons are only meaningful when all of these stay fixed:

- same PDF slice
- same parser version
- same parser mode and flags
- same GPU allocation
