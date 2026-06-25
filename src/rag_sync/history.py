from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def estimate_from_live_progress(elapsed_seconds: float, progress: float | None) -> int | None:
    if progress is None:
        return None
    if progress >= 1:
        return 0
    if progress <= 0:
        return None
    estimated_total = elapsed_seconds / progress
    remaining = max(0, estimated_total - elapsed_seconds)
    return int(round(remaining))


def estimate_from_history(
    rows: Iterable[Mapping[str, Any]],
    *,
    profile_name: str,
    source_type: str,
    parser: str,
    stage: str,
) -> int | None:
    durations = [
        float(row["duration_seconds"])
        for row in rows
        if row.get("profile_name") == profile_name
        and row.get("source_type") == source_type
        and row.get("parser") == parser
        and row.get("stage") == stage
        and row.get("duration_seconds") is not None
    ]
    if not durations:
        return None
    return int(round(sum(durations) / len(durations)))


def format_eta_seconds(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remainder = minutes % 60
    return f"{hours}h {remainder}m"
