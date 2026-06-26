from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rag_sync import __version__
from rag_sync.ldd import log_event_to_path
from rag_sync.parsers import build_marker_command
from rag_sync.scanner import pdf_metadata, sha256_file


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
    marker_bin: str = "marker",
) -> dict[str, object]:
    if work_dir.exists():
        shutil.rmtree(work_dir)
    staged_input_dir = work_dir / ".input"
    staged_input_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, staged_input_dir / pdf_path.name)

    markdown_path = output_dir / "outputs" / f"{pdf_path.stem}.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    raw_output_path = work_dir / "raw" / markdown_path.name
    command = build_marker_command(staged_input_dir, raw_output_path)
    command[0] = marker_bin

    started = time.monotonic()
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
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
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def run_batch(
    *,
    input_dir: Path,
    output_dir: Path,
    profile: str,
    tags: tuple[str, ...] = (),
    marker_bin: str = "marker",
) -> BatchRunResult:
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    log_path = output_dir / "logs" / "run.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_paths = sorted(input_dir.glob("*.pdf"))
    log_event_to_path(
        log_path,
        "batch.run.started",
        "ok",
        batch_id=batch_id,
        profile=profile,
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        file_count=len(pdf_paths),
    )

    manifest_records: list[dict[str, object]] = []
    success_count = 0
    failure_count = 0

    for pdf_path in pdf_paths:
        started_at = datetime.now(UTC)
        source_stat = pdf_path.stat()
        source_info = {
            "source_filename": pdf_path.name,
            "source_relpath": str(pdf_path.relative_to(input_dir)),
            "source_abspath_cluster": str(pdf_path.resolve()),
            "source_sha256": sha256_file(pdf_path),
            "source_size_bytes": source_stat.st_size,
            "source_mtime": source_stat.st_mtime,
            "page_count": pdf_metadata(pdf_path).get("page_count"),
        }
        work_dir = output_dir / ".work" / pdf_path.stem
        log_event_to_path(
            log_path,
            "file.convert.started",
            "ok",
            batch_id=batch_id,
            source_relpath=source_info["source_relpath"],
            source_path=str(pdf_path),
        )

        try:
            result = run_marker_for_file(
                pdf_path=pdf_path,
                output_dir=output_dir,
                work_dir=work_dir,
                marker_bin=marker_bin,
            )
            markdown_path = Path(str(result["markdown_path"]))
            returncode = int(result.get("returncode", 1))
            duration_seconds = float(result.get("duration_seconds", 0.0))
            finished_at = started_at.timestamp() + duration_seconds
            if returncode == 0 and markdown_path.exists():
                markdown_sha256 = sha256_file(markdown_path)
                markdown_size_bytes = markdown_path.stat().st_size
                status = "ok"
                error_type = None
                error_message = None
                success_count += 1
                log_event_to_path(
                    log_path,
                    "file.convert.finished",
                    "ok",
                    batch_id=batch_id,
                    source_relpath=source_info["source_relpath"],
                    markdown_relpath=str(markdown_path.relative_to(output_dir)),
                    returncode=returncode,
                    duration_seconds=duration_seconds,
                )
            else:
                markdown_sha256 = ""
                markdown_size_bytes = 0
                status = "error"
                error_type = "marker_failed"
                error_message = str(result.get("stderr", ""))[-1000:] or "marker failed"
                failure_count += 1
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
                )
        except Exception as exc:
            markdown_path = output_dir / "outputs" / f"{pdf_path.stem}.md"
            returncode = None
            duration_seconds = (datetime.now(UTC) - started_at).total_seconds()
            finished_at = datetime.now(UTC).timestamp()
            markdown_sha256 = ""
            markdown_size_bytes = 0
            status = "error"
            error_type = type(exc).__name__
            error_message = str(exc)
            failure_count += 1
            log_event_to_path(
                log_path,
                "file.convert.failed",
                "error",
                batch_id=batch_id,
                source_relpath=source_info["source_relpath"],
                duration_seconds=duration_seconds,
                error_type=error_type,
                error_message=error_message,
            )

        manifest_records.append(
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
            }
        )

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
            "1",
        ],
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
