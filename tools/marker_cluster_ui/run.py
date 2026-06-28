from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

import psutil
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.text import Text

from rag_sync.marker_batch import BatchRunResult, run_batch

CLI_RUNTIME_ERROR_EXIT_CODE = 2
POLL_INTERVAL_SECONDS = 0.5
RECENT_FAILURE_LIMIT = 5
INPUT_PREVIEW_LIMIT = 6
RECENT_COMPLETION_LIMIT = 8


@dataclass(frozen=True)
class CompletionRecord:
    source_relpath: str
    duration_seconds: float | None
    status: str
    gpu_device: str | None = None


@dataclass
class LiveBatchState:
    batch_id: str | None = None
    total_files: int = 0
    processed_files: int = 0
    success_count: int = 0
    failure_count: int = 0
    current_file: str | None = None
    current_file_started_at: datetime | None = None
    last_event_at: datetime | None = None
    recent_failures: list[str] = field(default_factory=list)
    recent_completions: list[CompletionRecord] = field(default_factory=list)
    log_lines_processed: int = 0

    @property
    def started(self) -> bool:
        return self.batch_id is not None


@dataclass(frozen=True)
class GpuSnapshot:
    index: str
    name: str
    utilization_gpu: str
    memory_used_mb: str
    memory_total_mb: str


@dataclass(frozen=True)
class ResourceSnapshot:
    system_cpu_percent: float
    system_ram_used_gb: float
    system_ram_total_gb: float
    self_rss_gb: float
    child_rss_gb: float
    child_cpu_percent: float
    gpus: tuple[GpuSnapshot, ...]


_SELF_PROCESS = psutil.Process(os.getpid())
_SELF_PROCESS.cpu_percent(interval=None)


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_timeout(seconds: int) -> str:
    if seconds <= 0:
        return "disabled"
    return _format_duration(float(seconds))


def _format_gb(value: float) -> str:
    return f"{value:.1f} GB"


def _sample_resources() -> ResourceSnapshot:
    vm = psutil.virtual_memory()
    system_cpu_percent = psutil.cpu_percent(interval=None)
    self_rss_gb = _SELF_PROCESS.memory_info().rss / (1024**3)

    child_rss_bytes = 0
    child_cpu_percent = 0.0
    for child in _SELF_PROCESS.children(recursive=True):
        with child.oneshot():
            child_rss_bytes += child.memory_info().rss
            child_cpu_percent += child.cpu_percent(interval=None)

    return ResourceSnapshot(
        system_cpu_percent=system_cpu_percent,
        system_ram_used_gb=vm.used / (1024**3),
        system_ram_total_gb=vm.total / (1024**3),
        self_rss_gb=self_rss_gb,
        child_rss_gb=child_rss_bytes / (1024**3),
        child_cpu_percent=child_cpu_percent,
        gpus=_query_gpus(),
    )


def _query_gpus() -> tuple[GpuSnapshot, ...]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ()

    snapshots: list[GpuSnapshot] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",", maxsplit=4)]
        if len(parts) != 5:
            continue
        snapshots.append(
            GpuSnapshot(
                index=parts[0],
                name=parts[1],
                utilization_gpu=parts[2],
                memory_used_mb=parts[3],
                memory_total_mb=parts[4],
            )
        )
    return tuple(snapshots)


def _tail_batch_log(log_path: Path, state: LiveBatchState) -> int:
    if not log_path.exists():
        return 0
    processed = 0
    with log_path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index < state.log_lines_processed:
                continue
            if not line.strip():
                continue
            record = json.loads(line)
            _apply_log_record(state, record)
            processed += 1
    state.log_lines_processed += processed
    return processed


def _apply_log_record(state: LiveBatchState, record: dict[str, object]) -> None:
    event = str(record.get("event", ""))
    ts = _parse_ts(record.get("ts"))
    if ts is not None:
        state.last_event_at = ts

    if event == "batch.run.started":
        state.batch_id = str(record.get("batch_id", "")) or None
        file_count = record.get("file_count")
        if isinstance(file_count, int):
            state.total_files = file_count
        return

    if event == "file.convert.started":
        state.current_file = str(record.get("source_relpath", ""))
        state.current_file_started_at = ts
        return

    if event == "file.convert.finished":
        state.processed_files += 1
        state.success_count += 1
        state.recent_completions.append(
            CompletionRecord(
                source_relpath=str(record.get("source_relpath", "")) or "<unknown>",
                duration_seconds=(
                    float(record["duration_seconds"])
                    if isinstance(record.get("duration_seconds"), int | float)
                    else None
                ),
                status="ok",
                gpu_device=str(record.get("gpu_device", "")) or None,
            )
        )
        state.recent_completions = state.recent_completions[-RECENT_COMPLETION_LIMIT:]
        state.current_file = None
        state.current_file_started_at = None
        return

    if event == "file.convert.failed":
        state.processed_files += 1
        state.failure_count += 1
        source_relpath = str(record.get("source_relpath", "")) or "<unknown>"
        error_message = str(record.get("error_message", "")) or "conversion failed"
        state.recent_failures.append(f"{source_relpath}: {error_message}")
        state.recent_failures = state.recent_failures[-RECENT_FAILURE_LIMIT:]
        state.recent_completions.append(
            CompletionRecord(
                source_relpath=source_relpath,
                duration_seconds=(
                    float(record["duration_seconds"])
                    if isinstance(record.get("duration_seconds"), int | float)
                    else None
                ),
                status="failed",
                gpu_device=str(record.get("gpu_device", "")) or None,
            )
        )
        state.recent_completions = state.recent_completions[-RECENT_COMPLETION_LIMIT:]
        state.current_file = None
        state.current_file_started_at = None


def _render_live_view(
    *,
    args: argparse.Namespace,
    state: LiveBatchState,
    started_at: float,
    input_preview: list[str],
    resources: ResourceSnapshot,
) -> Table:
    now = time.monotonic()
    elapsed_seconds = now - started_at
    processing_seconds = None
    if state.current_file_started_at is not None:
        processing_seconds = max(
            0.0,
            (datetime.now(tz=state.current_file_started_at.tzinfo) - state.current_file_started_at).total_seconds(),
        )
    average_seconds = (
        elapsed_seconds / state.processed_files if state.processed_files > 0 else None
    )
    rate = state.processed_files / elapsed_seconds if elapsed_seconds > 0 else 0.0
    eta_seconds = None
    if state.total_files > 0 and state.processed_files > 0 and state.processed_files < state.total_files:
        eta_seconds = (state.total_files - state.processed_files) / max(rate, 1e-9)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Converting[/bold blue]"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TextColumn("{task.completed}/{task.total} files"),
        expand=True,
    )
    total = max(state.total_files, 1)
    progress.add_task("batch", total=total, completed=min(state.processed_files, total))

    metrics = Table.grid(padding=(0, 2))
    metrics.add_column(style="bold cyan", no_wrap=True)
    metrics.add_column()
    metrics.add_row("Profile", str(args.profile))
    metrics.add_row("Batch", state.batch_id or "starting...")
    metrics.add_row("Parallel", str(args.parallel_files))
    metrics.add_row("GPU map", args.gpu_devices or "auto")
    metrics.add_row("Timeout", _format_timeout(args.timeout_seconds))
    metrics.add_row("Input", str(args.input_dir))
    metrics.add_row("Output", str(args.output_dir))
    metrics.add_row("Elapsed", _format_duration(elapsed_seconds))
    metrics.add_row("ETA", _format_duration(eta_seconds))
    metrics.add_row("Success", str(state.success_count))
    metrics.add_row("Failed", str(state.failure_count))
    metrics.add_row("Avg/file", _format_duration(average_seconds))
    metrics.add_row("Rate", f"{rate:.2f} files/s" if state.processed_files > 0 else "warming up")
    current_file = state.current_file or "waiting for first file..."
    if len(current_file) > 110:
        current_file = f"{current_file[:107]}..."
    metrics.add_row("Current", current_file)
    metrics.add_row("Current time", _format_duration(processing_seconds))

    failures = Table.grid()
    failures.add_column()
    if state.recent_failures:
        for item in state.recent_failures:
            failures.add_row(Text(item, style="red"))
    else:
        failures.add_row(Text("No failures yet", style="green"))

    completions = Table.grid(padding=(0, 1))
    completions.add_column(style="bold")
    completions.add_column()
    completions.add_column(justify="right")
    completions.add_column(no_wrap=True)
    if state.recent_completions:
        for item in reversed(state.recent_completions):
            label = item.source_relpath
            if len(label) > 58:
                label = f"{label[:55]}..."
            status_style = "green" if item.status == "ok" else "red"
            completions.add_row(
                Text(item.status.upper(), style=status_style),
                label,
                _format_duration(item.duration_seconds),
                f"gpu={item.gpu_device}" if item.gpu_device is not None else "",
            )
    else:
        completions.add_row(Text("No completed files yet", style="yellow"), "", "", "")

    discovered = Table.grid()
    discovered.add_column()
    if input_preview:
        for item in input_preview:
            discovered.add_row(item)
    else:
        discovered.add_row(Text("No PDFs discovered yet", style="yellow"))

    resource_rows = Table.grid(padding=(0, 2))
    resource_rows.add_column(style="bold green", no_wrap=True)
    resource_rows.add_column()
    resource_rows.add_row("CPU", f"{resources.system_cpu_percent:.0f}% system")
    resource_rows.add_row(
        "RAM",
        f"{_format_gb(resources.system_ram_used_gb)} / {_format_gb(resources.system_ram_total_gb)}",
    )
    resource_rows.add_row("Runner RSS", _format_gb(resources.self_rss_gb))
    resource_rows.add_row("Child RSS", _format_gb(resources.child_rss_gb))
    resource_rows.add_row("Child CPU", f"{resources.child_cpu_percent:.0f}%")
    if resources.gpus:
        for gpu in resources.gpus[:2]:
            resource_rows.add_row(
                f"GPU {gpu.index}",
                f"{gpu.utilization_gpu}% | {gpu.memory_used_mb}/{gpu.memory_total_mb} MB",
            )
    else:
        resource_rows.add_row("GPU", "nvidia-smi unavailable")

    layout = Table.grid(expand=True)
    layout.add_row(
        Panel(progress, title="Progress", border_style="blue"),
        Panel(metrics, title="Run Metrics", border_style="cyan"),
    )
    layout.add_row(
        Panel(discovered, title="Input Preview", border_style="magenta"),
        Panel(resource_rows, title="Resources", border_style="green"),
    )
    layout.add_row(Panel(completions, title="Recent Completions", border_style="green"))
    layout.add_row(Panel(failures, title="Recent Failures", border_style="red"))
    return layout


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Marker batch conversion and print a compact Rich summary."
    )
    parser.add_argument("--input-dir", type=Path, required=True, help="Directory containing PDFs.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where outputs, manifest, and logs are written.",
    )
    parser.add_argument("--profile", required=True, help="Profile name recorded in the manifest.")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Optional manifest tag. Repeat to add more than one tag.",
    )
    parser.add_argument(
        "--marker-bin",
        default="marker",
        help="Marker executable to invoke. Defaults to 'marker'.",
    )
    parser.add_argument(
        "--parallel-files",
        type=int,
        default=1,
        help="Number of PDFs to convert concurrently.",
    )
    parser.add_argument(
        "--marker-workers",
        type=int,
        default=1,
        help="Per-marker internal worker count.",
    )
    parser.add_argument(
        "--gpu-devices",
        default="",
        help="Comma-separated CUDA device ids to round-robin across, for example '0,1'.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="Per-file timeout for a single Marker conversion. Use 0 to disable.",
    )
    return parser.parse_args(argv)


def render_summary(console: Console, result: BatchRunResult) -> None:
    console.print(
        Panel.fit(
            "\n".join(
                [
                    f"batch_id: {result.batch_id}",
                    f"success_count: {result.success_count}",
                    f"failure_count: {result.failure_count}",
                    f"manifest_path: {result.manifest_path}",
                    f"log_path: {result.log_path}",
                ]
            ),
            title="Batch Summary",
        )
    )


def main(argv: Sequence[str] | None = None, *, console: Console | None = None) -> int:
    args = parse_args(argv)
    console = console or Console()
    gpu_devices = tuple(part.strip() for part in args.gpu_devices.split(",") if part.strip())
    console.print(
        f"[bold]Marker batch[/bold] input={args.input_dir} output={args.output_dir} profile={args.profile} "
        f"parallel_files={args.parallel_files} marker_workers={args.marker_workers} "
        f"gpu_devices={args.gpu_devices or 'auto'} timeout={_format_timeout(args.timeout_seconds)}"
    )
    input_preview = [
        path.name
        for path in sorted(args.input_dir.rglob("*.pdf"))[:INPUT_PREVIEW_LIMIT]
    ] if args.input_dir.exists() else []
    result: BatchRunResult | None = None
    failure: Exception | None = None

    def worker() -> None:
        nonlocal result, failure
        try:
            result = run_batch(
                input_dir=args.input_dir,
                output_dir=args.output_dir,
                profile=args.profile,
                tags=tuple(args.tag),
                marker_bin=args.marker_bin,
                parallel_files=args.parallel_files,
                marker_workers=args.marker_workers,
                gpu_devices=gpu_devices,
                timeout_seconds=args.timeout_seconds,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            failure = exc

    state = LiveBatchState()
    log_path = args.output_dir / "logs" / "run.jsonl"
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    started_at = time.monotonic()
    resources = _sample_resources()

    try:
        with Live(
            _render_live_view(
                args=args,
                state=state,
                started_at=started_at,
                input_preview=input_preview,
                resources=resources,
            ),
            console=console,
            refresh_per_second=4,
            transient=False,
        ) as live:
            while thread.is_alive():
                _tail_batch_log(log_path, state)
                resources = _sample_resources()
                live.update(
                    _render_live_view(
                        args=args,
                        state=state,
                        started_at=started_at,
                        input_preview=input_preview,
                        resources=resources,
                    )
                )
                thread.join(POLL_INTERVAL_SECONDS)
            _tail_batch_log(log_path, state)
            resources = _sample_resources()
            live.update(
                _render_live_view(
                    args=args,
                    state=state,
                    started_at=started_at,
                    input_preview=input_preview,
                    resources=resources,
                )
            )
    except KeyboardInterrupt:
        console.print("[bold red]Marker batch interrupted.[/bold red]")
        return CLI_RUNTIME_ERROR_EXIT_CODE

    if failure is not None:
        console.print(f"[bold red]Marker batch failed:[/bold red] {failure}")
        return CLI_RUNTIME_ERROR_EXIT_CODE
    if result is None:
        console.print("[bold red]Marker batch failed:[/bold red] batch terminated without a result")
        return CLI_RUNTIME_ERROR_EXIT_CODE
    render_summary(console, result)
    return 0 if result.failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
