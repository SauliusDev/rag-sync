from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QualityResult:
    status: str
    warnings: list[str]


def check_markdown_quality(path: Path, math_heavy: bool) -> QualityResult:
    text = path.read_text(encoding="utf-8", errors="replace")
    warnings: list[str] = []
    if not text.strip():
        return QualityResult(status="blocked", warnings=["generated markdown is empty"])
    if "�" in text:
        warnings.append("replacement characters detected")
    if "<!-- formula-not-decoded -->" in text:
        warnings.append("formula placeholder detected")
    if math_heavy and "$" not in text and "\\[" not in text:
        warnings.append("no obvious equations detected in math-heavy profile")
    status = "warning" if warnings else "clean"
    return QualityResult(status=status, warnings=warnings)
