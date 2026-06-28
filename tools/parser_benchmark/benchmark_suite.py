from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import threading
import time
import tomllib
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.parser_benchmark.benchmark_marker import (
    BENCHMARK_ROOT,
    DEFAULT_PAGE_COUNT,
    DEFAULT_PAGE_START,
    DEFAULT_SOURCE_PDF,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_OUTPUT_ROOT,
    extract_sample_pdf,
    log_event,
    stage_marker_input,
    validate_page_range,
)

DEFAULT_LOG_PATH = BENCHMARK_ROOT / "logs" / "parser-benchmark.jsonl"
DEFAULT_CONFIG_PATH = BENCHMARK_ROOT / "benchmark.toml"
DEFAULT_MONITOR_INTERVAL_SECONDS = 5
PARSER_TITLES = {
    "marker": "Marker",
    "mineru": "MinerU",
    "paddleocr-vl": "PaddleOCR-VL",
    "glmocr": "GLM-OCR",
}


@dataclass(frozen=True)
class ParserRun:
    parser: str
    command: list[str]
    output_dir: Path
    timeout_seconds: int
    version_command: list[str] | None
    env: dict[str, str] | None = None
    stage_input: bool = False
    sample_page_count: int = 0


def _default_marker_bin() -> str:
    local = BENCHMARK_ROOT / ".venvs" / "marker" / "bin" / "marker"
    if local.exists():
        return str(local)
    return shutil.which("marker") or "marker"


def _default_mineru_bin() -> str:
    local = BENCHMARK_ROOT / ".venvs" / "mineru" / "bin" / "mineru"
    if local.exists():
        return str(local)
    return shutil.which("mineru") or "mineru"


def _default_paddleocr_bin() -> str:
    local = BENCHMARK_ROOT / ".venvs" / "paddleocr" / "bin" / "paddleocr"
    if local.exists():
        return str(local)
    return shutil.which("paddleocr") or "paddleocr"


def _default_glmocr_bin() -> str:
    local = BENCHMARK_ROOT / ".venvs" / "glmocr" / "bin" / "glmocr"
    if local.exists():
        return str(local)
    return shutil.which("glmocr") or "glmocr"


def _adjacent_python(bin_path: str) -> str | None:
    if "/" not in bin_path:
        return None
    python_path = Path(bin_path).resolve().parent / "python"
    if python_path.exists():
        return str(python_path)
    return None


def default_settings() -> dict[str, Any]:
    paddle_bin = _default_paddleocr_bin()
    glm_bin = _default_glmocr_bin()
    return {
        "benchmark": {
            "monitor_interval_seconds": DEFAULT_MONITOR_INTERVAL_SECONDS,
            "log_path": str(DEFAULT_LOG_PATH),
        },
        "parsers": {
            "marker": {
                "enabled": True,
                "bin": _default_marker_bin(),
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                "disable_ocr": True,
                "workers": 1,
                "version_command": None,
            },
            "mineru": {
                "enabled": True,
                "bin": _default_mineru_bin(),
                "timeout_seconds": 2400,
                "backend": "hybrid-engine",
                "effort": "high",
                "env": {
                    "CUDA_VISIBLE_DEVICES": "1",
                    "VLLM_USE_V1": "1",
                    "VLLM_USE_FLASHINFER_SAMPLER": "0",
                },
                "extra_args": [],
                "version_command": [str(_default_mineru_bin()), "--version"],
            },
            "paddleocr_vl": {
                "enabled": True,
                "bin": paddle_bin,
                "timeout_seconds": 2400,
                "pipeline_version": "v1.5",
                "device": "gpu",
                "engine": "",
                "vl_rec_backend": "",
                "vl_rec_server_url": "",
                "vl_rec_api_model_name": "",
                "vl_rec_api_key": "",
                "extra_args": [],
                "version_command": _python_import_version_command(paddle_bin, "paddleocr"),
            },
            "glmocr": {
                "enabled": True,
                "bin": glm_bin,
                "timeout_seconds": 2400,
                "config_path": "",
                "layout_device": "",
                "env": {
                    "CUDA_VISIBLE_DEVICES": "0",
                    "VLLM_USE_V1": "1",
                    "VLLM_USE_FLASHINFER_SAMPLER": "0",
                },
                "extra_args": [],
                "version_command": _python_import_version_command(glm_bin, "glmocr"),
            },
        },
    }


def _python_import_version_command(bin_path: str, module_name: str) -> list[str] | None:
    python_path = _adjacent_python(bin_path)
    if python_path is None:
        return None
    return [
        python_path,
        "-c",
        (
            "import importlib; "
            f"mod = importlib.import_module('{module_name}'); "
            "print(getattr(mod, '__version__', 'unknown'))"
        ),
    ]


def load_settings(config_path: Path | None) -> dict[str, Any]:
    settings = default_settings()
    if config_path is None or not config_path.exists():
        return settings
    loaded = tomllib.loads(config_path.read_text(encoding="utf-8"))
    merged = _deep_merge(settings, loaded)
    return _resolve_relative_paths(merged, base_dir=config_path.parent.resolve())


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_relative_paths(settings: dict[str, Any], *, base_dir: Path) -> dict[str, Any]:
    benchmark = settings.get("benchmark", {})
    log_path = benchmark.get("log_path")
    if isinstance(log_path, str) and log_path:
        benchmark["log_path"] = str(_resolve_path(log_path, base_dir))
    for parser_config in settings.get("parsers", {}).values():
        for key in ("bin", "config_path"):
            value = parser_config.get(key)
            if isinstance(value, str) and value:
                parser_config[key] = str(_resolve_path(value, base_dir))
        version_command = parser_config.get("version_command")
        if isinstance(version_command, list) and version_command:
            first = version_command[0]
            if isinstance(first, str):
                parser_config["version_command"][0] = str(_resolve_path(first, base_dir))
    return settings


def _resolve_path(raw_path: str, base_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def build_marker_command(
    marker_bin: str,
    input_dir: Path,
    output_dir: Path,
    *,
    disable_ocr: bool = True,
    workers: int = 1,
) -> list[str]:
    command = [
        marker_bin,
        str(input_dir),
        "--output_dir",
        str(output_dir),
        "--output_format",
        "markdown",
        "--disable_image_extraction",
        "--workers",
        str(workers),
    ]
    if disable_ocr:
        command.insert(6, "--disable_ocr")
    return command


def build_mineru_command(
    mineru_bin: str,
    source_pdf: Path,
    output_dir: Path,
    *,
    backend: str = "hybrid-engine",
    effort: str = "high",
    extra_args: Sequence[str] = (),
) -> list[str]:
    command = [
        mineru_bin,
        "-p",
        str(source_pdf),
        "-o",
        str(output_dir),
        "-b",
        backend,
    ]
    if effort:
        command.extend(["--effort", effort])
    command.extend(str(item) for item in extra_args)
    return command


def build_paddleocr_command(
    paddleocr_bin: str,
    source_pdf: Path,
    output_dir: Path,
    *,
    pipeline_version: str = "v1.5",
    device: str = "gpu",
    engine: str = "",
    vl_rec_backend: str = "",
    vl_rec_server_url: str = "",
    vl_rec_api_model_name: str = "",
    vl_rec_api_key: str = "",
    extra_args: Sequence[str] = (),
) -> list[str]:
    command = [
        paddleocr_bin,
        "doc_parser",
        "-i",
        str(source_pdf),
        "--save_path",
        str(output_dir),
    ]
    if pipeline_version:
        command.extend(["--pipeline_version", pipeline_version])
    if device:
        command.extend(["--device", device])
    if engine:
        command.extend(["--engine", engine])
    if vl_rec_backend:
        command.extend(["--vl_rec_backend", vl_rec_backend])
    if vl_rec_server_url:
        command.extend(["--vl_rec_server_url", vl_rec_server_url])
    if vl_rec_api_model_name:
        command.extend(["--vl_rec_api_model_name", vl_rec_api_model_name])
    if vl_rec_api_key:
        command.extend(["--vl_rec_api_key", vl_rec_api_key])
    command.extend(str(item) for item in extra_args)
    return command


def build_glmocr_command(
    glmocr_bin: str,
    source_pdf: Path,
    output_dir: Path,
    *,
    config_path: Path | None = None,
    layout_device: str = "",
    extra_args: Sequence[str] = (),
) -> list[str]:
    command = [
        glmocr_bin,
        "parse",
        str(source_pdf),
        "--output",
        str(output_dir),
    ]
    if config_path:
        command.extend(["--config", str(config_path)])
    if layout_device:
        command.extend(["--layout-device", layout_device])
    command.extend(str(item) for item in extra_args)
    return command


def scan_output_stats(output_dir: Path) -> dict[str, Any]:
    markdown_paths = sorted(path for path in output_dir.rglob("*.md") if path.is_file())
    json_paths = sorted(path for path in output_dir.rglob("*.json") if path.is_file())
    largest_markdown = max(markdown_paths, key=lambda path: path.stat().st_size, default=None)
    return {
        "markdown_count": len(markdown_paths),
        "markdown_bytes": sum(path.stat().st_size for path in markdown_paths),
        "json_count": len(json_paths),
        "json_bytes": sum(path.stat().st_size for path in json_paths),
        "largest_markdown_path": (
            str(largest_markdown.relative_to(output_dir)) if largest_markdown is not None else None
        ),
        "largest_markdown_bytes": largest_markdown.stat().st_size if largest_markdown else 0,
    }


def estimate_progress_percent(
    *,
    parser_name: str,
    output_stats: dict[str, Any],
    sample_page_count: int,
) -> float | None:
    if sample_page_count < 1:
        return None
    if parser_name == "paddleocr-vl" and output_stats["markdown_count"] > 0:
        return round(min(100.0, (output_stats["markdown_count"] / sample_page_count) * 100), 1)
    return None


def write_run_summary(run_dir: Path, summary: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "summary.md").write_text(_summary_markdown(summary), encoding="utf-8")


def _summary_markdown(summary: dict[str, Any]) -> str:
    page_start = summary.get("page_start")
    page_end = summary.get("page_end")
    page_range = f"`{page_start}-{page_end}`" if page_start is not None and page_end is not None else "`n/a`"
    lines = [
        "# Parser Benchmark Summary",
        "",
        f"- Source PDF: `{summary['source_pdf']}`",
        f"- Sample PDF: `{summary['sample_pdf']}`",
        f"- Page range: {page_range}",
        f"- Sample page count: `{summary['sample_page_count']}`",
        "",
        "## Parsers",
        "",
    ]
    for parser_summary in summary.get("parsers", []):
        label = PARSER_TITLES.get(
            parser_summary["parser"],
            parser_summary["parser"].replace("_", " ").title(),
        )
        lines.extend(
            [
                f"### {label}",
                "",
                f"- Status: `{parser_summary['status']}`",
                f"- Duration seconds: `{parser_summary['duration_seconds']}`",
                f"- Pages per minute: `{parser_summary['pages_per_minute']}`",
                f"- Markdown files: `{parser_summary['markdown_count']}`",
                f"- Markdown bytes: `{parser_summary['markdown_bytes']}`",
                f"- JSON files: `{parser_summary.get('json_count', 0)}`",
                f"- Output directory: `{parser_summary.get('output_dir', 'n/a')}`",
                "",
            ]
        )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark document parsers on a fixed PDF sample.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--source-pdf", type=Path, default=DEFAULT_SOURCE_PDF)
    parser.add_argument("--page-start", type=int, default=DEFAULT_PAGE_START)
    parser.add_argument("--page-count", type=int, default=DEFAULT_PAGE_COUNT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--parsers",
        default="marker,mineru,paddleocr_vl,glmocr",
        help="Comma-separated parser ids to run.",
    )
    parser.add_argument("--monitor-interval-seconds", type=int, default=0)
    parser.add_argument("--list-parsers", action="store_true")
    return parser.parse_args(argv)


def build_parser_runs(
    *,
    settings: dict[str, Any],
    sample_pdf: Path,
    run_dir: Path,
    sample_page_count: int,
    selected_parsers: list[str],
) -> list[ParserRun]:
    parser_runs: list[ParserRun] = []
    parser_settings = settings["parsers"]
    for parser_name in selected_parsers:
        cfg = parser_settings.get(parser_name)
        if cfg is None or not cfg.get("enabled", True):
            continue
        parser_dir = run_dir / parser_name
        if parser_name == "marker":
            command = build_marker_command(
                cfg["bin"],
                parser_dir / ".input",
                parser_dir / "raw-output",
                disable_ocr=bool(cfg.get("disable_ocr", True)),
                workers=int(cfg.get("workers", 1)),
            )
            parser_runs.append(
                ParserRun(
                    parser="marker",
                    command=command,
                    output_dir=parser_dir / "raw-output",
                    timeout_seconds=int(cfg["timeout_seconds"]),
                    version_command=_string_list(cfg.get("version_command")),
                    stage_input=True,
                    sample_page_count=sample_page_count,
                )
            )
        elif parser_name == "mineru":
            command = build_mineru_command(
                cfg["bin"],
                sample_pdf,
                parser_dir / "raw-output",
                backend=str(cfg.get("backend", "hybrid-engine")),
                effort=str(cfg.get("effort", "high")),
                extra_args=_string_list(cfg.get("extra_args", [])),
            )
            parser_runs.append(
                ParserRun(
                    parser="mineru",
                    command=command,
                    output_dir=parser_dir / "raw-output",
                    timeout_seconds=int(cfg["timeout_seconds"]),
                    version_command=_string_list(cfg.get("version_command")),
                    env=_string_dict(cfg.get("env", {})),
                    sample_page_count=sample_page_count,
                )
            )
        elif parser_name == "paddleocr_vl":
            command = build_paddleocr_command(
                cfg["bin"],
                sample_pdf,
                parser_dir / "raw-output",
                pipeline_version=str(cfg.get("pipeline_version", "v1.5")),
                device=str(cfg.get("device", "gpu")),
                engine=str(cfg.get("engine", "")),
                vl_rec_backend=str(cfg.get("vl_rec_backend", "")),
                vl_rec_server_url=str(cfg.get("vl_rec_server_url", "")),
                vl_rec_api_model_name=str(cfg.get("vl_rec_api_model_name", "")),
                vl_rec_api_key=str(cfg.get("vl_rec_api_key", "")),
                extra_args=_string_list(cfg.get("extra_args", [])),
            )
            parser_runs.append(
                ParserRun(
                    parser="paddleocr-vl",
                    command=command,
                    output_dir=parser_dir / "raw-output",
                    timeout_seconds=int(cfg["timeout_seconds"]),
                    version_command=_string_list(cfg.get("version_command")),
                    env=_string_dict(cfg.get("env", {})),
                    sample_page_count=sample_page_count,
                )
            )
        elif parser_name == "glmocr":
            config_path = str(cfg.get("config_path", "")).strip()
            command = build_glmocr_command(
                cfg["bin"],
                sample_pdf,
                parser_dir / "raw-output",
                config_path=Path(config_path) if config_path else None,
                layout_device=str(cfg.get("layout_device", "")),
                extra_args=_string_list(cfg.get("extra_args", [])),
            )
            parser_runs.append(
                ParserRun(
                    parser="glmocr",
                    command=command,
                    output_dir=parser_dir / "raw-output",
                    timeout_seconds=int(cfg["timeout_seconds"]),
                    version_command=_string_list(cfg.get("version_command")),
                    sample_page_count=sample_page_count,
                )
            )
    return parser_runs


def _string_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _string_dict(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    return {str(key): str(item) for key, item in value.items()}


def capture_environment(parser_runs: list[ParserRun]) -> dict[str, Any]:
    return {
        "hostname": os.uname().nodename,
        "python": shutil.which("python3") or shutil.which("python") or "",
        "gpus": _query_gpu_snapshot(),
        "parser_versions": {
            parser_run.parser: _capture_version(parser_run.version_command)
            for parser_run in parser_runs
        },
    }


def _capture_version(command: list[str] | None) -> str | None:
    if not command:
        return None
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return None
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[0] if text else None


def _query_gpu_snapshot() -> list[dict[str, Any]]:
    if shutil.which("nvidia-smi") is None:
        return []
    command = [
        "nvidia-smi",
        "--query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        return []
    snapshots: list[dict[str, Any]] = []
    for raw_line in result.stdout.splitlines():
        parts = [part.strip() for part in raw_line.split(",")]
        if len(parts) != 7:
            continue
        snapshots.append(
            {
                "index": int(parts[0]),
                "name": parts[1],
                "gpu_util_percent": int(parts[2]),
                "memory_util_percent": int(parts[3]),
                "memory_used_mib": int(parts[4]),
                "memory_total_mib": int(parts[5]),
                "temperature_c": int(parts[6]),
            }
        )
    return snapshots


def run_parser(
    *,
    parser_run: ParserRun,
    sample_pdf: Path,
    log_path: Path,
    monitor_interval_seconds: int,
) -> dict[str, Any]:
    parser_run.output_dir.mkdir(parents=True, exist_ok=True)
    command = list(parser_run.command)
    if parser_run.stage_input:
        input_dir = stage_marker_input(sample_pdf, parser_run.output_dir)
        command = build_marker_command(
            command[0],
            input_dir,
            parser_run.output_dir,
            disable_ocr="--disable_ocr" in command,
            workers=_extract_workers(command),
        )

    executable = command[0]
    if shutil.which(executable) is None and not Path(executable).exists():
        error = f"parser executable not found: {executable}"
        log_event(
            log_path,
            "parser.run.failed",
            "error",
            parser=parser_run.parser,
            command=command,
            output_dir=parser_run.output_dir,
            error=error,
        )
        return {
            "parser": parser_run.parser,
            "status": "missing",
            "returncode": None,
            "duration_seconds": 0.0,
            "pages_per_minute": 0.0,
            "markdown_count": 0,
            "markdown_bytes": 0,
            "json_count": 0,
            "json_bytes": 0,
            "largest_markdown_path": None,
            "output_dir": str(parser_run.output_dir),
            "command": command,
            "error": error,
        }

    parser_started = time.monotonic()
    log_event(
        log_path,
        "parser.run.started",
        "ok",
        parser=parser_run.parser,
        command=command,
        output_dir=parser_run.output_dir,
        timeout_seconds=parser_run.timeout_seconds,
    )
    child_env = os.environ.copy()
    if parser_run.env:
        child_env.update(parser_run.env)
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
        start_new_session=True,
    )
    stop_event = threading.Event()
    monitor = threading.Thread(
        target=_monitor_process,
        kwargs={
            "proc": proc,
            "parser_name": parser_run.parser,
            "output_dir": parser_run.output_dir,
            "sample_page_count": parser_run.sample_page_count,
            "log_path": log_path,
            "started": parser_started,
            "interval_seconds": monitor_interval_seconds,
            "stop_event": stop_event,
        },
        daemon=True,
    )
    monitor.start()
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=parser_run.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        stdout, stderr = proc.communicate()
    finally:
        stop_event.set()
        monitor.join(timeout=monitor_interval_seconds + 1)

    duration_seconds = round(time.monotonic() - parser_started, 3)
    output_stats = scan_output_stats(parser_run.output_dir)
    status = "ok"
    error: str | None = None
    if timed_out:
        status = "timeout"
        error = f"{parser_run.parser} timed out after {parser_run.timeout_seconds}s"
    elif proc.returncode != 0:
        status = "error"
        error = f"{parser_run.parser} failed with return code {proc.returncode}"

    log_event(
        log_path,
        "parser.run.finished" if status == "ok" else "parser.run.failed",
        "ok" if status == "ok" else "error",
        parser=parser_run.parser,
        command=command,
        output_dir=parser_run.output_dir,
        duration_seconds=duration_seconds,
        returncode=proc.returncode,
        markdown_count=output_stats["markdown_count"],
        markdown_bytes=output_stats["markdown_bytes"],
        json_count=output_stats["json_count"],
        json_bytes=output_stats["json_bytes"],
        stdout_tail=stdout[-2000:],
        stderr_tail=stderr[-2000:],
        error=error,
    )
    pages_per_minute = round((parser_run.sample_page_count / duration_seconds) * 60, 2) if duration_seconds > 0 else 0.0
    return {
        "parser": parser_run.parser,
        "status": status,
        "returncode": proc.returncode,
        "duration_seconds": duration_seconds,
        "pages_per_minute": pages_per_minute,
        "markdown_count": output_stats["markdown_count"],
        "markdown_bytes": output_stats["markdown_bytes"],
        "json_count": output_stats["json_count"],
        "json_bytes": output_stats["json_bytes"],
        "largest_markdown_path": output_stats["largest_markdown_path"],
        "output_dir": str(parser_run.output_dir),
        "command": command,
        "error": error,
    }


def _extract_workers(command: list[str]) -> int:
    if "--workers" not in command:
        return 1
    index = command.index("--workers")
    if index + 1 >= len(command):
        return 1
    try:
        return int(command[index + 1])
    except ValueError:
        return 1


def _monitor_process(
    *,
    proc: subprocess.Popen[str],
    parser_name: str,
    output_dir: Path,
    sample_page_count: int,
    log_path: Path,
    started: float,
    interval_seconds: int,
    stop_event: threading.Event,
) -> None:
    if interval_seconds <= 0:
        return
    while not stop_event.wait(interval_seconds):
        output_stats = scan_output_stats(output_dir)
        log_event(
            log_path,
            "parser.progress",
            "ok",
            parser=parser_name,
            pid=proc.pid,
            elapsed_seconds=round(time.monotonic() - started, 3),
            poll=proc.poll(),
            estimated_percent=estimate_progress_percent(
                parser_name=parser_name,
                output_stats=output_stats,
                sample_page_count=sample_page_count,
            ),
            output_stats=output_stats,
            gpus=_query_gpu_snapshot(),
        )
        if proc.poll() is not None:
            return


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(args.config if args.config.exists() else None)
    selected_parsers = [item.strip() for item in args.parsers.split(",") if item.strip()]
    if args.list_parsers:
        for parser_name in settings["parsers"]:
            print(parser_name)
        return 0

    source_pdf = args.source_pdf.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    log_path = Path(settings["benchmark"]["log_path"]).expanduser().resolve()
    monitor_interval_seconds = (
        args.monitor_interval_seconds
        if args.monitor_interval_seconds > 0
        else int(settings["benchmark"]["monitor_interval_seconds"])
    )
    run_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    run_dir = output_root / "runs" / run_stamp
    page_end = args.page_start + args.page_count - 1
    sample_slug = source_pdf.stem.replace(" ", "_")
    sample_pdf = output_root / "samples" / f"{sample_slug}-p{args.page_start}-{page_end}.pdf"

    if not source_pdf.exists():
        raise FileNotFoundError(f"source pdf not found: {source_pdf}")

    log_event(
        log_path,
        "benchmark.run.started",
        "ok",
        source_pdf=source_pdf,
        sample_pdf=sample_pdf,
        page_start=args.page_start,
        page_end=page_end,
        run_dir=run_dir,
        selected_parsers=selected_parsers,
    )
    total_pages = extract_sample_pdf(source_pdf, sample_pdf, args.page_start, page_end)
    validate_page_range(total_pages, args.page_start, args.page_count)
    parser_runs = build_parser_runs(
        settings=settings,
        sample_pdf=sample_pdf,
        run_dir=run_dir,
        sample_page_count=args.page_count,
        selected_parsers=selected_parsers,
    )
    environment = capture_environment(parser_runs)
    (run_dir / "environment.json").parent.mkdir(parents=True, exist_ok=True)
    (run_dir / "environment.json").write_text(
        json.dumps(environment, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    parser_summaries = []
    for parser_run in parser_runs:
        parser_summaries.append(
            run_parser(
                parser_run=parser_run,
                sample_pdf=sample_pdf,
                log_path=log_path,
                monitor_interval_seconds=monitor_interval_seconds,
            )
        )

    summary = {
        "source_pdf": str(source_pdf),
        "sample_pdf": str(sample_pdf),
        "page_start": args.page_start,
        "page_end": page_end,
        "sample_page_count": args.page_count,
        "run_dir": str(run_dir),
        "environment": environment,
        "parsers": parser_summaries,
    }
    write_run_summary(run_dir, summary)
    log_event(
        log_path,
        "benchmark.run.finished",
        "ok",
        source_pdf=source_pdf,
        sample_pdf=sample_pdf,
        run_dir=run_dir,
        summary_json=run_dir / "summary.json",
        summary_md=run_dir / "summary.md",
    )
    return 0
