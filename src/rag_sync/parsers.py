from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rag_sync.artifacts import make_upload_markdown, make_upload_markdown_from_text

MARKER_BIN = "/home/saulius/atlas-parser-benchmark/.venvs/marker/bin/marker"
MINERU_BIN = "/home/saulius/atlas-parser-benchmark/.venvs/mineru/bin/mineru"


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
        raw_output_path = raw_dir / output_path.name
        cmd = build_marker_command(source_path, raw_output_path)
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
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
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"mineru failed for {source_path}: {proc.stderr[-1000:]}")
        raw = _single_markdown_output(raw_dir, self.name, source_path)
        _wrap_parser_output(raw, source_path, output_path, source_type, self.name, sha256)
        return ParserResult(self.name, output_path, proc.stdout, proc.stderr)
