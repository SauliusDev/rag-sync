from __future__ import annotations

import json
from pathlib import Path


def _frontmatter_value(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


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
        f"source_path: {_frontmatter_value(source_path)}\n"
        f"source_type: {_frontmatter_value(source_type)}\n"
        f"parser: {_frontmatter_value(parser)}\n"
        f"sha256: {_frontmatter_value(sha256)}\n"
        "---\n\n"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(frontmatter + body, encoding="utf-8", newline="\n")
    return output_path
