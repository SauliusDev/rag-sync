from __future__ import annotations

from pathlib import Path


def make_upload_markdown(
    source_path: Path,
    output_path: Path,
    source_type: str,
    parser: str,
    sha256: str,
) -> Path:
    body = source_path.read_text(encoding="utf-8", errors="replace")
    frontmatter = (
        "---\n"
        f"source_path: {source_path}\n"
        f"source_type: {source_type}\n"
        f"parser: {parser}\n"
        f"sha256: {sha256}\n"
        "---\n\n"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(frontmatter + body, encoding="utf-8", newline="\n")
    return output_path
