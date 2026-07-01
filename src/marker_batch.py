from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src import __version__
from src.ldd import log_event_to_path
from src.parsers import build_marker_command, _should_disable_marker_ocr_for_pdf
from src.scanner import pdf_metadata, sha256_file

MARKER_BATCH_TIMEOUT_SECONDS = int(os.environ.get("RAG_SYNC_MARKER_TIMEOUT_SECONDS", "0"))


@dataclass(frozen=True)
class BatchRunResult:
    batch_id: str
    success_count: int
    failure_count: int
    manifest_path: Path
    log_path: Path


def run_marker_for_file(
    *,
    pdf_path: Path,
    output_dir: Path,
    work_dir: Path,
    markdown_path: Path,
    marker_bin: str = "marker",
    marker_workers: int = 1,
    gpu_device: str | None = None,
    timeout_seconds: int = MARKER_BATCH_TIMEOUT_SECONDS,
) -> dict[str, object]:
    if work_dir.exists():
        shutil.rmtree(work_dir)
    staged_input_dir = work_dir / ".input"
    staged_input_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, staged_input_dir / pdf_path.name)

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    raw_output_path = work_dir / "raw" / markdown_path.name
    metadata = pdf_metadata(pdf_path)
    disable_ocr = _should_disable_marker_ocr_for_pdf(
        source_path=pdf_path,
        pdf_producer=str(metadata.get("pdf_producer", "")),
    )
    command = build_marker_command(
        staged_input_dir,
        raw_output_path,
        disable_ocr=disable_ocr,
        workers=marker_workers,
    )
    command[0] = marker_bin
    env = os.environ.copy()
    if gpu_device is not None:
        env["CUDA_VISIBLE_DEVICES"] = gpu_device

    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=env,
    )
    try:
        try:
            if timeout_seconds > 0:
                stdout, stderr = proc.communicate(timeout=timeout_seconds)
            else:
                stdout, stderr = proc.communicate()
        except subprocess.TimeoutExpired as exc:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            raise RuntimeError(f"marker timed out after {timeout_seconds}s") from exc
    finally:
        duration_seconds = time.monotonic() - started

    raw_output_dir = raw_output_path.parent
    if proc.returncode == 0:
        discovered = sorted(raw_output_dir.rglob("*.md"))
        chosen_markdown: Path | None = None
        if len(discovered) == 1:
            chosen_markdown = discovered[0]
        elif raw_output_path.exists():
            chosen_markdown = raw_output_path
        if chosen_markdown is not None:
            shutil.copy2(chosen_markdown, markdown_path)

    return {
        "command": command,
        "markdown_path": markdown_path,
        "returncode": proc.returncode,
        "duration_seconds": duration_seconds,
        "stdout": stdout,
        "stderr": stderr,
        "gpu_device": gpu_device,
    }


def run_batch(
    *,
    input_dir: Path,
    output_dir: Path,
    profile: str,
    tags: tuple[str, ...] = (),
    marker_bin: str = "marker",
    parallel_files: int = 1,
    marker_workers: int = 1,
    gpu_devices: tuple[str, ...] = (),
    timeout_seconds: int = MARKER_BATCH_TIMEOUT_SECONDS,
) -> BatchRunResult:
    input_dir = input_dir.resolve()
    if not input_dir.exists():
        raise ValueError(f"input directory does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise ValueError(f"input path is not a directory: {input_dir}")
    if parallel_files < 1:
        raise ValueError("parallel_files must be at least 1")
    if marker_workers < 1:
        raise ValueError("marker_workers must be at least 1")
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be at least 0")

    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    log_path = output_dir / "logs" / "run.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_paths = sorted(
        path for path in input_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"
    )
    if not pdf_paths:
        raise ValueError(f"input directory contains no PDF files: {input_dir}")
    log_event_to_path(
        log_path,
        "batch.run.started",
        "ok",
        batch_id=batch_id,
        profile=profile,
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        file_count=len(pdf_paths),
        parallel_files=parallel_files,
        marker_workers=marker_workers,
        gpu_devices=list(gpu_devices),
        timeout_seconds=timeout_seconds,
    )

    manifest_records: list[dict[str, object] | None] = [None] * len(pdf_paths)
    success_count = 0
    failure_count = 0

    def process_pdf(index: int, pdf_path: Path) -> tuple[int, dict[str, object], bool]:
        started_at = datetime.now(UTC)
        source_relpath = pdf_path.relative_to(input_dir)
        source_stat = pdf_path.stat()
        source_info = {
            "source_filename": pdf_path.name,
            "source_relpath": str(source_relpath),
            "source_abspath_cluster": str(pdf_path.resolve()),
            "source_sha256": sha256_file(pdf_path),
            "source_size_bytes": source_stat.st_size,
            "source_mtime": source_stat.st_mtime,
            "page_count": pdf_metadata(pdf_path).get("page_count"),
        }
        markdown_path = output_dir / "outputs" / source_relpath.with_suffix(".md")
        work_dir = output_dir / ".work" / source_relpath.with_suffix("")
        gpu_device = gpu_devices[index % len(gpu_devices)] if gpu_devices else None
        log_event_to_path(
            log_path,
            "file.convert.started",
            "ok",
            batch_id=batch_id,
            source_relpath=source_info["source_relpath"],
            source_path=str(pdf_path),
            gpu_device=gpu_device,
        )

        try:
            result = run_marker_for_file(
                pdf_path=pdf_path,
                output_dir=output_dir,
                work_dir=work_dir,
                markdown_path=markdown_path,
                marker_bin=marker_bin,
                marker_workers=marker_workers,
                gpu_device=gpu_device,
                timeout_seconds=timeout_seconds,
            )
            markdown_path = Path(str(result["markdown_path"]))
            returncode = int(result.get("returncode", 1))
            duration_seconds = float(result.get("duration_seconds", 0.0))
            finished_at = started_at.timestamp() + duration_seconds
            succeeded = returncode == 0 and markdown_path.exists()
            if succeeded:
                markdown_sha256 = sha256_file(markdown_path)
                markdown_size_bytes = markdown_path.stat().st_size
                status = "ok"
                error_type = None
                error_message = None
                log_event_to_path(
                    log_path,
                    "file.convert.finished",
                    "ok",
                    batch_id=batch_id,
                    source_relpath=source_info["source_relpath"],
                    markdown_relpath=str(markdown_path.relative_to(output_dir)),
                    returncode=returncode,
                    duration_seconds=duration_seconds,
                    gpu_device=gpu_device,
                )
            else:
                markdown_sha256 = "missing"
                markdown_size_bytes = 0
                status = "error"
                error_type = "marker_failed"
                error_message = str(result.get("stderr", ""))[-1000:] or "marker failed"
                log_event_to_path(
                    log_path,
                    "file.convert.failed",
                    "error",
                    batch_id=batch_id,
                    source_relpath=source_info["source_relpath"],
                    returncode=returncode,
                    duration_seconds=duration_seconds,
                    error_type=error_type,
                    error_message=error_message,
                    gpu_device=gpu_device,
                )
        except Exception as exc:
            returncode = None
            duration_seconds = (datetime.now(UTC) - started_at).total_seconds()
            finished_at = datetime.now(UTC).timestamp()
            markdown_sha256 = "missing"
            markdown_size_bytes = 0
            status = "error"
            error_type = type(exc).__name__
            error_message = str(exc)
            succeeded = False
            log_event_to_path(
                log_path,
                "file.convert.failed",
                "error",
                batch_id=batch_id,
                source_relpath=source_info["source_relpath"],
                duration_seconds=duration_seconds,
                error_type=error_type,
                error_message=error_message,
                gpu_device=gpu_device,
            )

        return index, (
            {
                **source_info,
                "markdown_relpath": str(markdown_path.relative_to(output_dir)),
                "markdown_sha256": markdown_sha256,
                "markdown_size_bytes": markdown_size_bytes,
                "status": status,
                "started_at": started_at.isoformat(timespec="seconds"),
                "finished_at": datetime.fromtimestamp(finished_at, UTC).isoformat(
                    timespec="seconds"
                ),
                "duration_seconds": duration_seconds,
                "returncode": returncode,
                "error_type": error_type,
                "error_message": error_message,
                "gpu_device": gpu_device,
            }
        ), succeeded

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_files) as executor:
        futures = [
            executor.submit(process_pdf, index, pdf_path)
            for index, pdf_path in enumerate(pdf_paths)
        ]
        for future in concurrent.futures.as_completed(futures):
            index, record, succeeded = future.result()
            manifest_records[index] = record
            if succeeded:
                success_count += 1
            else:
                failure_count += 1

    manifest = {
        "batch_id": batch_id,
        "created_at": created_at,
        "host": socket.gethostname(),
        "profile": profile,
        "tags": list(tags),
        "parser": "marker",
        "parser_version": _marker_version(marker_bin),
        "parser_flags": [
            "--output_format",
            "markdown",
            "--disable_ocr",
            "--disable_image_extraction",
            "--workers",
            str(marker_workers),
        ],
        "parallel_files": parallel_files,
        "gpu_devices": list(gpu_devices),
        "timeout_seconds": timeout_seconds,
        "files": manifest_records,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    log_event_to_path(
        log_path,
        "manifest.write.finished",
        "ok",
        batch_id=batch_id,
        manifest_path=str(manifest_path),
        file_count=len(manifest_records),
    )
    log_event_to_path(
        log_path,
        "batch.run.finished",
        "ok" if failure_count == 0 else "error",
        batch_id=batch_id,
        success_count=success_count,
        failure_count=failure_count,
        manifest_path=str(manifest_path),
    )
    return BatchRunResult(
        batch_id=batch_id,
        success_count=success_count,
        failure_count=failure_count,
        manifest_path=manifest_path,
        log_path=log_path,
    )


def _marker_version(marker_bin: str) -> str:
    try:
        proc = subprocess.run(
            [marker_bin, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return f"unknown ({__version__})"

    output = (proc.stdout or proc.stderr).strip()
    if output:
        return output.splitlines()[0]
    return f"unknown ({__version__})"
