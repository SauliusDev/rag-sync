from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from rag_sync.artifacts import make_upload_markdown, make_upload_markdown_from_text

MARKER_BIN = "/home/saulius/atlas-parser-benchmark/.venvs/marker/bin/marker"
MINERU_BIN = "/home/saulius/atlas-parser-benchmark/.venvs/mineru/bin/mineru"
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
    if not candidates:
        raise RuntimeError(f"{parser} produced no markdown for {source_path}")
    if len(candidates) > 1:
        names = ", ".join(str(path.relative_to(raw_dir)) for path in candidates[:5])
        raise RuntimeError(f"{parser} produced multiple markdown files for {source_path}: {names}")
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


def _run_parser_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _register_parser_process(proc)
    try:
        stdout, stderr = proc.communicate()
    finally:
        _unregister_parser_process(proc)
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode or 0,
        stdout=stdout,
        stderr=stderr,
    )


def build_marker_command(source_path: Path, output_path: Path) -> list[str]:
    marker_bin = MARKER_BIN if Path(MARKER_BIN).exists() else shutil.which("marker") or "marker"
    return [
        marker_bin,
        str(source_path),
        "--output_dir",
        str(output_path.parent),
        "--output_format",
        "markdown",
        "--disable_ocr",
        "--disable_image_extraction",
        "--workers",
        "1",
    ]


class MarkerParser:
    name = "marker"

    def convert(
        self,
        source_path: Path,
        output_path: Path,
        source_type: str,
        sha256: str,
    ) -> ParserResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        raw_dir = _prepare_raw_output_dir(output_path, self.name)
        input_dir = raw_dir / ".input"
        input_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, input_dir / source_path.name)
        raw_output_path = raw_dir / output_path.name
        cmd = build_marker_command(input_dir, raw_output_path)
        proc = _run_parser_command(cmd)
        if proc.returncode != 0:
            raise RuntimeError(f"marker failed for {source_path}: {proc.stderr[-1000:]}")
        raw = _single_markdown_output(raw_dir, self.name, source_path)
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
        proc = _run_parser_command(cmd)
        if proc.returncode != 0:
            raise RuntimeError(f"mineru failed for {source_path}: {proc.stderr[-1000:]}")
        raw = _single_markdown_output(raw_dir, self.name, source_path)
        _wrap_parser_output(raw, source_path, output_path, source_type, self.name, sha256)
        return ParserResult(self.name, output_path, proc.stdout, proc.stderr)
