from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
from threading import Lock

from pypdf import PdfReader

from rag_sync.artifacts import make_upload_markdown, make_upload_markdown_from_text
from rag_sync.ldd import log_event

MARKER_BIN = "/home/saulius/atlas-parser-benchmark/.venvs/marker/bin/marker"
MINERU_BIN = "/home/saulius/atlas-parser-benchmark/.venvs/mineru/bin/mineru"
MARKER_TIMEOUT_SECONDS = int(os.environ.get("RAG_SYNC_MARKER_TIMEOUT_SECONDS", "1200"))
MINERU_TIMEOUT_SECONDS = int(os.environ.get("RAG_SYNC_MINERU_TIMEOUT_SECONDS", "1200"))
MARKER_LOW_MEMORY_OCR = os.environ.get("RAG_SYNC_MARKER_LOW_MEMORY_OCR", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
_active_parser_procs: set[subprocess.Popen[str]] = set()
_active_parser_procs_lock = Lock()


@dataclass(frozen=True)
class ParserResult:
    parser: str
    output_path: Path
    stdout: str
    stderr: str


class PassthroughParser:
    name = "passthrough"

    def convert(
        self,
        source_path: Path,
        output_path: Path,
        source_type: str,
        sha256: str,
        pdf_producer: str = "",
    ) -> ParserResult:
        make_upload_markdown(source_path, output_path, source_type, self.name, sha256)
        return ParserResult(self.name, output_path, "", "")


def _raw_output_dir(output_path: Path, parser: str) -> Path:
    return output_path.parent / ".parser-raw" / parser / output_path.stem


def _prepare_raw_output_dir(output_path: Path, parser: str) -> Path:
    raw_dir = _raw_output_dir(output_path, parser)
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir


def _single_markdown_output(raw_dir: Path, parser: str, source_path: Path) -> Path:
    candidates = sorted(raw_dir.rglob("*.md"))
    log_event(
        "parser.output.scanned",
        "ok",
        parser=parser,
        source_path=str(source_path),
        raw_dir=str(raw_dir),
        markdown_count=len(candidates),
    )
    if not candidates:
        log_event(
            "parser.output.missing",
            "error",
            parser=parser,
            source_path=str(source_path),
            raw_dir=str(raw_dir),
        )
        raise RuntimeError(f"{parser} produced no markdown for {source_path}")
    if len(candidates) > 1:
        names = ", ".join(str(path.relative_to(raw_dir)) for path in candidates[:5])
        log_event(
            "parser.output.ambiguous",
            "error",
            parser=parser,
            source_path=str(source_path),
            raw_dir=str(raw_dir),
            markdown_count=len(candidates),
            candidates=names,
        )
        raise RuntimeError(f"{parser} produced multiple markdown files for {source_path}: {names}")
    log_event(
        "parser.output.selected",
        "ok",
        parser=parser,
        source_path=str(source_path),
        raw_dir=str(raw_dir),
        markdown_path=str(candidates[0]),
        markdown_bytes=candidates[0].stat().st_size,
    )
    return candidates[0]


def _wrap_parser_output(
    raw_markdown: Path,
    source_path: Path,
    output_path: Path,
    source_type: str,
    parser: str,
    sha256: str,
) -> Path:
    body = raw_markdown.read_text(encoding="utf-8", errors="replace")
    return make_upload_markdown_from_text(
        body,
        source_path,
        output_path,
        source_type,
        parser,
        sha256,
    )


def _register_parser_process(proc: subprocess.Popen[str]) -> None:
    with _active_parser_procs_lock:
        _active_parser_procs.add(proc)


def _unregister_parser_process(proc: subprocess.Popen[str]) -> None:
    with _active_parser_procs_lock:
        _active_parser_procs.discard(proc)


def terminate_active_parser_processes() -> int:
    with _active_parser_procs_lock:
        procs = list(_active_parser_procs)
    terminated = 0
    for proc in procs:
        if proc.poll() is not None:
            _unregister_parser_process(proc)
            continue
        try:
            os.killpg(proc.pid, 15)
            terminated += 1
        except ProcessLookupError:
            pass
        finally:
            _unregister_parser_process(proc)
    return terminated


def _run_parser_command(
    cmd: list[str],
    *,
    parser_name: str,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    log_event(
        "parser.command.started",
        "ok",
        parser=parser_name,
        command=cmd,
        timeout_seconds=timeout_seconds,
    )
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _register_parser_process(proc)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            log_event(
                "parser.command.timeout",
                "error",
                parser=parser_name,
                command=cmd,
                pid=proc.pid,
                timeout_seconds=timeout_seconds,
                duration_seconds=time.monotonic() - started,
            )
            raise RuntimeError(f"{parser_name} timed out after {timeout_seconds}s")
    finally:
        _unregister_parser_process(proc)
    log_event(
        "parser.command.finished",
        "ok" if (proc.returncode or 0) == 0 else "error",
        parser=parser_name,
        command=cmd,
        pid=proc.pid,
        returncode=proc.returncode or 0,
        stdout_bytes=len(stdout.encode("utf-8")),
        stderr_bytes=len(stderr.encode("utf-8")),
        duration_seconds=time.monotonic() - started,
    )
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode or 0,
        stdout=stdout,
        stderr=stderr,
    )


def _should_disable_marker_ocr_for_pdf(source_path: Path, pdf_producer: str = "") -> bool:
    producer = pdf_producer.strip().lower()
    likely_scanned = "pdfcompressor" in producer or "scan" in producer or "ocr" in producer
    try:
        reader = PdfReader(source_path)
        page_count = len(reader.pages)
        sample_indices = sorted(
            {
                0,
                1,
                2,
                max(page_count // 2, 0),
                max(page_count - 3, 0),
                max(page_count - 2, 0),
                max(page_count - 1, 0),
            }
        )
        sampled = 0
        text_pages = 0
        blank_pages = 0
        normalized_samples: list[str] = []
        for index in sample_indices:
            if index >= page_count:
                continue
            sampled += 1
            page = reader.pages[index]
            text = page.extract_text() or ""
            stripped = text.strip()
            if len(stripped) >= 30:
                text_pages += 1
                normalized_samples.append(re.sub(r"\s+", " ", stripped).lower()[:240])
            else:
                blank_pages += 1
    except Exception:
        log_event(
            "marker.ocr_decision.failed",
            "error",
            parser="marker",
            source_path=str(source_path),
            pdf_producer=pdf_producer,
            likely_scanned=likely_scanned,
        )
        return not likely_scanned
    repeated_text_pages = 0
    if normalized_samples:
        dominant_sample, dominant_count = Counter(normalized_samples).most_common(1)[0]
        if dominant_count >= max(3, sampled - 1):
            repeated_text_pages = dominant_count
    effective_text_pages = text_pages - repeated_text_pages
    disable_ocr = effective_text_pages > 0 and effective_text_pages >= blank_pages
    log_event(
        "marker.ocr_decision.finished",
        "ok",
        parser="marker",
        source_path=str(source_path),
        pdf_producer=pdf_producer,
        page_count=page_count,
        sampled_pages=sampled,
        text_pages=text_pages,
        effective_text_pages=effective_text_pages,
        blank_pages=blank_pages,
        repeated_text_pages=repeated_text_pages,
        disable_ocr=disable_ocr,
    )
    return disable_ocr


def build_marker_command(
    source_path: Path,
    output_path: Path,
    *,
    disable_ocr: bool = True,
) -> list[str]:
    marker_bin = MARKER_BIN if Path(MARKER_BIN).exists() else shutil.which("marker") or "marker"
    command = [
        marker_bin,
        str(source_path),
        "--output_dir",
        str(output_path.parent),
        "--output_format",
        "markdown",
        "--disable_image_extraction",
        "--workers",
        "1",
    ]
    if disable_ocr:
        command.insert(6, "--disable_ocr")
    elif MARKER_LOW_MEMORY_OCR:
        command.extend(
            [
                "--disable_multiprocessing",
                "--lowres_image_dpi",
                "72",
                "--highres_image_dpi",
                "96",
                "--layout_batch_size",
                "1",
                "--detection_batch_size",
                "1",
                "--recognition_batch_size",
                "1",
                "--ocr_error_batch_size",
                "1",
                "--ocr_task_name",
                "ocr_without_boxes",
                "--drop_repeated_text",
            ]
        )
    return command


class MarkerParser:
    name = "marker"

    def convert(
        self,
        source_path: Path,
        output_path: Path,
        source_type: str,
        sha256: str,
        pdf_producer: str = "",
    ) -> ParserResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        raw_dir = _prepare_raw_output_dir(output_path, self.name)
        input_dir = raw_dir / ".input"
        input_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, input_dir / source_path.name)
        raw_output_path = raw_dir / output_path.name
        disable_ocr = True
        if source_path.suffix.lower() == ".pdf":
            disable_ocr = _should_disable_marker_ocr_for_pdf(
                source_path=source_path,
                pdf_producer=pdf_producer,
            )
        cmd = build_marker_command(input_dir, raw_output_path, disable_ocr=disable_ocr)
        proc = _run_parser_command(
            cmd,
            parser_name=self.name,
            timeout_seconds=MARKER_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            log_event(
                "parser.failed",
                "error",
                parser=self.name,
                source_path=str(source_path),
                output_path=str(output_path),
                raw_dir=str(raw_dir),
                returncode=proc.returncode,
                stderr_tail=proc.stderr[-1000:],
            )
            raise RuntimeError(f"marker failed for {source_path}: {proc.stderr[-1000:]}")
        try:
            raw = _single_markdown_output(raw_dir, self.name, source_path)
        except RuntimeError:
            log_event(
                "parser.failed",
                "error",
                parser=self.name,
                source_path=str(source_path),
                output_path=str(output_path),
                raw_dir=str(raw_dir),
                returncode=proc.returncode,
                stderr_tail=proc.stderr[-2000:],
            )
            raise
        _wrap_parser_output(raw, source_path, output_path, source_type, self.name, sha256)
        return ParserResult(self.name, output_path, proc.stdout, proc.stderr)


class MinerUParser:
    name = "mineru"

    def convert(
        self,
        source_path: Path,
        output_path: Path,
        source_type: str,
        sha256: str,
        pdf_producer: str = "",
    ) -> ParserResult:
        mineru_bin = MINERU_BIN if Path(MINERU_BIN).exists() else shutil.which("mineru") or "mineru"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        raw_dir = _prepare_raw_output_dir(output_path, self.name)
        cmd = [
            mineru_bin,
            "--path",
            str(source_path),
            "--output",
            str(raw_dir),
            "--backend",
            "pipeline",
            "--method",
            "txt",
            "--formula",
            "true",
            "--table",
            "true",
        ]
        proc = _run_parser_command(
            cmd,
            parser_name=self.name,
            timeout_seconds=MINERU_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            log_event(
                "parser.failed",
                "error",
                parser=self.name,
                source_path=str(source_path),
                output_path=str(output_path),
                raw_dir=str(raw_dir),
                returncode=proc.returncode,
                stderr_tail=proc.stderr[-1000:],
            )
            raise RuntimeError(f"mineru failed for {source_path}: {proc.stderr[-1000:]}")
        raw = _single_markdown_output(raw_dir, self.name, source_path)
        _wrap_parser_output(raw, source_path, output_path, source_type, self.name, sha256)
        return ParserResult(self.name, output_path, proc.stdout, proc.stderr)
