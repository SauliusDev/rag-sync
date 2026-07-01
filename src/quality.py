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


def check_markdown_quality(
    path: Path,
    math_heavy: bool,
    page_count: int | None = None,
) -> QualityResult:
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
    body_bytes = len(body.encode("utf-8"))
    word_count = len(body.split())
    if math_heavy and page_count is not None and page_count >= 10:
        bytes_per_page = body_bytes / page_count
        words_per_page = word_count / page_count
        if body_bytes < 12_000 or (page_count >= 30 and bytes_per_page < 800 and words_per_page < 100):
            warnings.append(
                "implausibly small markdown for multi-page math-heavy source"
            )
            return QualityResult(status="blocked", warnings=warnings)
    status = "warning" if warnings else "clean"
    return QualityResult(status=status, warnings=warnings)
