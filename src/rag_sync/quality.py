from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

QualityStatus = Literal["clean", "warning", "blocked"]


@dataclass(frozen=True)
class QualityResult:
    status: QualityStatus
    warnings: list[str]


def _body_without_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    _, separator, body = text[4:].partition("\n---\n")
    if not separator:
        return text
    return body


def check_markdown_quality(path: Path, math_heavy: bool) -> QualityResult:
    text = path.read_text(encoding="utf-8", errors="replace")
    body = _body_without_frontmatter(text)
    warnings: list[str] = []
    if not body.strip():
        return QualityResult(status="blocked", warnings=["generated markdown is empty"])
    if "�" in text:
        warnings.append("replacement characters detected")
    if "<!-- formula-not-decoded -->" in text:
        warnings.append("formula placeholder detected")
    if math_heavy and "$" not in body and "\\[" not in body:
        warnings.append("no obvious equations detected in math-heavy profile")
    status = "warning" if warnings else "clean"
    return QualityResult(status=status, warnings=warnings)
