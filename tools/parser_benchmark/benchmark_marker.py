from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from pypdf import PdfReader, PdfWriter

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = REPO_ROOT / "tools" / "parser-benchmark"
DEFAULT_SOURCE_PDF = Path(
    "/home/saulius/atlas/notes/quant/books/Matrix Cookbook - Kaare Brandt Petersen & Michael Syskind Pedersen.pdf"
)
LOCAL_MARKER_BIN = Path("/home/saulius/atlas-parser-benchmark/.venvs/marker/bin/marker")
DEFAULT_PAGE_START = 1
DEFAULT_PAGE_COUNT = 10
DEFAULT_TIMEOUT_SECONDS = 1200
DEFAULT_LOG_PATH = BENCHMARK_ROOT / "logs" / "marker-benchmark.jsonl"
DEFAULT_OUTPUT_ROOT = BENCHMARK_ROOT / "artifacts"


def validate_page_range(total_pages: int, page_start: int, page_count: int) -> tuple[int, int]:
    if page_start < 1:
        raise ValueError("page_start must be >= 1")
    if page_count < 1:
        raise ValueError("page_count must be >= 1")
    page_end = page_start + page_count - 1
    if page_end > total_pages:
        raise ValueError("page range exceeds total pages")
    return page_start, page_end


def log_event(log_path: Path, event: str, status: str, **fields: object) -> None:
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "event": event,
        "status": status,
        **{key: _json_value(value) for key, value in fields.items()},
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _json_value(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def extract_sample_pdf(source_pdf: Path, sample_pdf: Path, page_start: int, page_end: int) -> int:
    reader = PdfReader(str(source_pdf))
    writer = PdfWriter()
    total_pages = len(reader.pages)
    sample_pdf.parent.mkdir(parents=True, exist_ok=True)
    for page_number in range(page_start - 1, page_end):
        writer.add_page(reader.pages[page_number])
    with sample_pdf.open("wb") as handle:
        writer.write(handle)
    return total_pages


def stage_marker_input(sample_pdf: Path, raw_output_dir: Path) -> Path:
    input_dir = raw_output_dir / ".input"
    input_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sample_pdf, input_dir / sample_pdf.name)
    return input_dir


def build_marker_command(marker_bin: str, input_dir: Path, output_dir: Path) -> list[str]:
    return [
        marker_bin,
        str(input_dir),
        "--output_dir",
        str(output_dir),
        "--output_format",
        "markdown",
        "--disable_ocr",
        "--disable_image_extraction",
        "--workers",
        "1",
    ]


def run_marker(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def find_single_markdown_output(output_dir: Path) -> Path:
    candidates = sorted(output_dir.rglob("*.md"))
    if not candidates:
        raise RuntimeError(f"marker produced no markdown for {output_dir}")
    if len(candidates) > 1:
        raise RuntimeError(f"marker produced multiple markdown files for {output_dir}")
    return candidates[0]


def build_summary(
    *,
    source_pdf: Path,
    sample_pdf: Path,
    run_dir: Path,
    page_start: int,
    page_end: int,
    command: list[str],
    duration_seconds: float,
    returncode: int,
    markdown_path: Path | None,
) -> dict[str, object]:
    return {
        "source_pdf": str(source_pdf),
        "sample_pdf": str(sample_pdf),
        "run_dir": str(run_dir),
        "page_start": page_start,
        "page_end": page_end,
        "command": command,
        "duration_seconds": duration_seconds,
        "returncode": returncode,
        "markdown_path": str(markdown_path) if markdown_path else None,
        "markdown_bytes": markdown_path.stat().st_size if markdown_path else 0,
    }


def write_summary_files(run_dir: Path, summary: dict[str, object]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_json_path = run_dir / "summary.json"
    summary_md_path = run_dir / "summary.md"
    summary_json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary_md_path.write_text(_summary_markdown(summary), encoding="utf-8")


def _summary_markdown(summary: dict[str, object]) -> str:
    command = summary["command"]
    command_text = " ".join(command) if isinstance(command, list) else str(command)
    return "\n".join(
        [
            "# Marker Benchmark Summary",
            "",
            f"- Source PDF: `{summary['source_pdf']}`",
            f"- Sample PDF: `{summary['sample_pdf']}`",
            f"- Page range: `{summary['page_start']}-{summary['page_end']}`",
            f"- Duration seconds: `{summary['duration_seconds']}`",
            f"- Return code: `{summary['returncode']}`",
            f"- Markdown path: `{summary['markdown_path']}`",
            f"- Markdown bytes: `{summary['markdown_bytes']}`",
            f"- Run directory: `{summary['run_dir']}`",
            "",
            "## Command",
            "",
            f"`{command_text}`",
            "",
        ]
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark Marker on a fixed PDF sample.")
    parser.add_argument("--source-pdf", type=Path, default=DEFAULT_SOURCE_PDF)
    parser.add_argument("--page-start", type=int, default=DEFAULT_PAGE_START)
    parser.add_argument("--page-count", type=int, default=DEFAULT_PAGE_COUNT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--marker-bin", default=_default_marker_bin())
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args(argv)


def _default_marker_bin() -> str:
    if LOCAL_MARKER_BIN.exists():
        return str(LOCAL_MARKER_BIN)
    return shutil.which("marker") or "marker"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    source_pdf = args.source_pdf.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    log_path = DEFAULT_LOG_PATH
    run_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    sample_slug = source_pdf.stem.replace(" ", "_")
    run_dir = output_root / "runs" / run_stamp
    sample_pdf = output_root / "samples" / f"{sample_slug}-p{args.page_start}-{args.page_start + args.page_count - 1}.pdf"
    raw_output_dir = run_dir / "marker-output"
    page_end = args.page_start + args.page_count - 1
    benchmark_started = datetime.now(UTC)

    log_event(
        log_path,
        "benchmark.run.started",
        "ok",
        source_pdf=source_pdf,
        sample_pdf=sample_pdf,
        page_start=args.page_start,
        page_end=page_end,
        run_dir=run_dir,
    )

    try:
        if not source_pdf.exists():
            raise FileNotFoundError(f"source pdf not found: {source_pdf}")

        log_event(
            log_path,
            "sample.extract.started",
            "ok",
            source_pdf=source_pdf,
            sample_pdf=sample_pdf,
            page_start=args.page_start,
            page_end=page_end,
            run_dir=run_dir,
        )
        total_pages = extract_sample_pdf(source_pdf, sample_pdf, args.page_start, page_end)
        validate_page_range(total_pages, args.page_start, args.page_count)
        log_event(
            log_path,
            "sample.extract.finished",
            "ok",
            source_pdf=source_pdf,
            sample_pdf=sample_pdf,
            page_start=args.page_start,
            page_end=page_end,
            total_pages=total_pages,
            run_dir=run_dir,
        )

        input_dir = stage_marker_input(sample_pdf, raw_output_dir)
        command = build_marker_command(args.marker_bin, input_dir, raw_output_dir)
        marker_started = datetime.now(UTC)
        log_event(
            log_path,
            "marker.run.started",
            "ok",
            source_pdf=source_pdf,
            sample_pdf=sample_pdf,
            page_start=args.page_start,
            page_end=page_end,
            run_dir=run_dir,
            command=command,
            timeout_seconds=args.timeout_seconds,
        )
        try:
            result = run_marker(command, args.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            log_event(
                log_path,
                "marker.run.failed",
                "error",
                source_pdf=source_pdf,
                sample_pdf=sample_pdf,
                page_start=args.page_start,
                page_end=page_end,
                run_dir=run_dir,
                command=command,
                error_type=type(exc).__name__,
                error=str(exc),
                timeout_seconds=args.timeout_seconds,
            )
            raise RuntimeError(f"marker timed out after {args.timeout_seconds}s") from exc

        duration_seconds = (datetime.now(UTC) - marker_started).total_seconds()
        if result.returncode != 0:
            log_event(
                log_path,
                "marker.run.failed",
                "error",
                source_pdf=source_pdf,
                sample_pdf=sample_pdf,
                page_start=args.page_start,
                page_end=page_end,
                run_dir=run_dir,
                command=command,
                returncode=result.returncode,
                duration_seconds=duration_seconds,
                stderr_tail=result.stderr[-1000:],
            )
            raise RuntimeError(f"marker failed with return code {result.returncode}")

        log_event(
            log_path,
            "marker.run.finished",
            "ok",
            source_pdf=source_pdf,
            sample_pdf=sample_pdf,
            page_start=args.page_start,
            page_end=page_end,
            run_dir=run_dir,
            command=command,
            returncode=result.returncode,
            duration_seconds=duration_seconds,
            stdout_bytes=len(result.stdout.encode("utf-8")),
            stderr_bytes=len(result.stderr.encode("utf-8")),
        )

        markdown_path = find_single_markdown_output(raw_output_dir)
        log_event(
            log_path,
            "marker.output.scanned",
            "ok",
            source_pdf=source_pdf,
            sample_pdf=sample_pdf,
            page_start=args.page_start,
            page_end=page_end,
            run_dir=run_dir,
            markdown_path=markdown_path,
            markdown_bytes=markdown_path.stat().st_size,
        )

        summary = build_summary(
            source_pdf=source_pdf,
            sample_pdf=sample_pdf,
            run_dir=run_dir,
            page_start=args.page_start,
            page_end=page_end,
            command=command,
            duration_seconds=(datetime.now(UTC) - benchmark_started).total_seconds(),
            returncode=result.returncode,
            markdown_path=markdown_path,
        )
        log_event(
            log_path,
            "report.write.started",
            "ok",
            source_pdf=source_pdf,
            sample_pdf=sample_pdf,
            page_start=args.page_start,
            page_end=page_end,
            run_dir=run_dir,
        )
        write_summary_files(run_dir, summary)
        log_event(
            log_path,
            "report.write.finished",
            "ok",
            source_pdf=source_pdf,
            sample_pdf=sample_pdf,
            page_start=args.page_start,
            page_end=page_end,
            run_dir=run_dir,
            summary_json=run_dir / "summary.json",
            summary_md=run_dir / "summary.md",
        )
        return 0
    except Exception as exc:
        log_event(
            log_path,
            "benchmark.run.failed",
            "error",
            source_pdf=source_pdf,
            sample_pdf=sample_pdf,
            page_start=args.page_start,
            page_end=page_end,
            run_dir=run_dir,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise
