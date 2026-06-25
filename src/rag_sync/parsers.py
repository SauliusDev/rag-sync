from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rag_sync.artifacts import make_upload_markdown

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
        cmd = build_marker_command(source_path, output_path)
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"marker failed for {source_path}: {proc.stderr[-1000:]}")
        candidates = sorted(output_path.parent.rglob("*.md"))
        if not candidates:
            raise RuntimeError(f"marker produced no markdown for {source_path}")
        raw = candidates[0]
        body = raw.read_text(encoding="utf-8", errors="replace")
        tmp_body = output_path.parent / f"{output_path.stem}.body.md"
        tmp_body.write_text(body, encoding="utf-8")
        make_upload_markdown(tmp_body, output_path, source_type, self.name, sha256)
        tmp_body.unlink(missing_ok=True)
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
        cmd = [
            mineru_bin,
            "--path",
            str(source_path),
            "--output",
            str(output_path.parent),
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
        candidates = sorted(output_path.parent.rglob("*.md"))
        if not candidates:
            raise RuntimeError(f"mineru produced no markdown for {source_path}")
        raw = candidates[0]
        tmp_body = output_path.parent / f"{output_path.stem}.body.md"
        tmp_body.write_text(raw.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        make_upload_markdown(tmp_body, output_path, source_type, self.name, sha256)
        tmp_body.unlink(missing_ok=True)
        return ParserResult(self.name, output_path, proc.stdout, proc.stderr)
