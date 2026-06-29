from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
import re
from statistics import median
from typing import Any

from rag_sync.config import DEFAULT_DATA_DIR

MB = 1_000_000


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


def apply_live_glm_ocr_progress(
    job: Mapping[str, Any],
    source_row: Mapping[str, Any],
    *,
    output_root: Path = DEFAULT_DATA_DIR / "outputs",
) -> dict[str, Any]:
    updated = dict(job)
    if str(job.get("status", "")) != "running":
        return updated
    if str(job.get("kind", "")) not in {"sync_file", "convert"}:
        return updated
    if str(source_row.get("extension", "")).lower() != "pdf":
        return updated
    page_count = _positive_int(source_row.get("page_count"))
    if page_count is None:
        return updated

    raw_dir = _live_glm_ocr_render_dir(source_row, output_root=output_root)
    if raw_dir is None or not raw_dir.exists():
        return updated
    rendered_pages = len(list(raw_dir.glob("page_*.png")))
    if rendered_pages <= 0:
        return updated

    completed_pages = max(0, min(page_count, rendered_pages - 1))
    progress = max(0.0, min(0.999, completed_pages / page_count))
    updated["progress"] = progress
    updated["live_stage"] = "convert"
    updated["live_progress_percent"] = max(1, min(99, int(round(progress * 100))))
    updated["live_progress_pages_done"] = completed_pages
    updated["live_progress_page_count"] = page_count
    updated["live_progress_detail"] = f"{completed_pages}/{page_count} OCR pages"
    return updated


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


def size_band_for_bytes(size_bytes: int) -> str:
    if size_bytes < MB:
        return "0-1MB"
    if size_bytes < 10 * MB:
        return "1-10MB"
    if size_bytes < 50 * MB:
        return "10-50MB"
    if size_bytes < 200 * MB:
        return "50-200MB"
    return "200MB+"


def page_count_band(page_count: int | None) -> str:
    if page_count is None or page_count <= 0:
        return "unknown-pages"
    if page_count <= 25:
        return "1-25p"
    if page_count <= 100:
        return "26-100p"
    if page_count <= 250:
        return "101-250p"
    if page_count <= 500:
        return "251-500p"
    return "500p+"


def prepare_timing_context(db: Any) -> dict[str, Any]:
    rows = _history_rows(db)
    return {
        "baselines": _baselines_from_rows(rows),
        "page_rate_baselines": _page_rate_baselines_from_rows(rows),
        "throughput_seconds": _recent_throughput_seconds_from_rows(rows),
        "glm_ocr_usage_baseline": _glm_ocr_usage_baseline(db),
    }


def estimate_job_timing(
    db: Any,
    job: Mapping[str, Any],
    source_row: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    timing_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    stages = _remaining_stages_for_job(job, source_row)
    timing_source_row = _timing_source_row_for_job(job, source_row)
    if not stages:
        return {
            "eta_seconds": None,
            "eta_label": "unknown",
            "confidence": "estimating",
            "timing_basis": "unknown",
            "progress_percent": None,
        }

    live_page_estimate = _remaining_from_live_page_rate(
        db,
        job,
        timing_source_row,
        timing_context=timing_context,
    )
    live_seconds = _remaining_from_live_progress(job, now=now)
    total_seconds = 0
    confidences: list[str] = []
    bases: list[str] = []
    progress_percent: int | None = None
    for index, stage in enumerate(stages):
        if index == 0 and live_page_estimate is not None:
            seconds = live_page_estimate["seconds"]
            confidence = str(live_page_estimate["confidence"])
            basis = str(live_page_estimate["timing_basis"])
            progress_percent = _job_progress_percent(job)
        elif index == 0 and live_seconds is not None:
            seconds = live_seconds
            confidence = "live"
            basis = "live_progress"
            progress_percent = _job_progress_percent(job)
        elif index == 0:
            running_estimate = _running_stage_estimate_without_progress(
                db,
                job,
                timing_source_row,
                stage,
                now=now,
                timing_context=timing_context,
            )
            if running_estimate is not None:
                seconds = running_estimate["seconds"]
                confidence = str(running_estimate["confidence"])
                basis = str(running_estimate["timing_basis"])
                progress_percent = running_estimate["progress_percent"]
                if seconds is None:
                    return {
                        "eta_seconds": None,
                        "eta_label": "unknown",
                        "confidence": confidence,
                        "timing_basis": basis,
                        "progress_percent": None,
                    }
            else:
                estimate = _lookup_stage_estimate(db, timing_source_row, stage, timing_context=timing_context)
                seconds = estimate["seconds"]
                confidence = str(estimate["confidence"])
                basis = str(estimate["timing_basis"])
                if seconds is None:
                    fallback_seconds = _throughput_seconds(db, timing_context)
                    if fallback_seconds is not None:
                        seconds = fallback_seconds
                        confidence = "low"
                        basis = "recent_median"
        else:
            estimate = _lookup_stage_estimate(db, timing_source_row, stage, timing_context=timing_context)
            seconds = estimate["seconds"]
            confidence = str(estimate["confidence"])
            basis = str(estimate["timing_basis"])
            if seconds is None:
                fallback_seconds = _throughput_seconds(db, timing_context)
                if fallback_seconds is not None:
                    seconds = fallback_seconds
                    confidence = "low"
                    basis = "recent_median"
        if seconds is None:
            return {
                "eta_seconds": None,
                "eta_label": "unknown",
                "confidence": "estimating",
                "timing_basis": "unknown",
                "progress_percent": progress_percent,
            }
        total_seconds += int(seconds)
        confidences.append(confidence)
        bases.append(basis)
    return {
        "eta_seconds": total_seconds,
        "eta_label": f"{format_eta_seconds(total_seconds)} remaining",
        "confidence": _combine_confidence(confidences),
        "timing_basis": " -> ".join(bases),
        "progress_percent": progress_percent,
    }


def estimate_queue_timing(
    db: Any,
    jobs: list[dict[str, Any]],
    files_by_id: dict[int, dict[str, Any]],
    *,
    now: str | datetime | None = None,
    timing_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    total_seconds = 0
    confidences: list[str] = []
    throughput_seconds = _throughput_seconds(db, timing_context)
    api_cost_estimate = _estimate_queue_glm_ocr_api_cost(
        db,
        jobs,
        files_by_id,
        timing_context=timing_context,
    )
    for job in jobs:
        status = str(job.get("status", ""))
        if status not in {"running", "queued"}:
            continue
        source_file_id = job.get("source_file_id")
        source_row = files_by_id.get(int(source_file_id)) if source_file_id is not None else None
        if source_row is None:
            seconds = throughput_seconds
        else:
            estimate = estimate_job_timing(db, job, source_row, now=now, timing_context=timing_context)
            seconds = estimate["eta_seconds"]
        if seconds is None:
            if throughput_seconds is None:
                eta = _unknown_queue_eta(throughput_seconds)
                eta.update(api_cost_estimate)
                return eta
            seconds = throughput_seconds
            confidences.append("low")
        elif source_row is None:
            confidences.append("low")
        else:
            confidences.append(str(estimate["confidence"]))
        total_seconds += int(seconds)

    if total_seconds == 0 and not confidences:
        eta = _unknown_queue_eta(throughput_seconds)
        eta.update(api_cost_estimate)
        return eta
    eta = {
        "seconds": total_seconds,
        "label": f"{format_eta_seconds(total_seconds)} remaining",
        "confidence": _combine_confidence(confidences),
        "estimated_finish_at": _estimated_finish_at(total_seconds, now=now),
        "throughput_label": (
            "unknown throughput"
            if throughput_seconds is None
            else f"recent median {format_eta_seconds(throughput_seconds)}/file"
        ),
    }
    eta.update(api_cost_estimate)
    return eta


def _stage_for_job(job: Mapping[str, Any]) -> str | None:
    kind = str(job.get("kind", ""))
    if kind in {"sync_file", "convert"}:
        return "convert"
    if kind == "upload":
        return "upload"
    if kind == "parse":
        return "parse"
    return None


def _remaining_stages_for_job(
    job: Mapping[str, Any],
    source_row: Mapping[str, Any],
) -> list[str]:
    if job.get("live_stage") == "convert":
        return ["convert", "upload", "parse"] if str(job.get("kind", "")) == "sync_file" else ["convert"]

    kind = str(job.get("kind", ""))
    if kind != "sync_file":
        stage = _stage_for_job(job)
        return [] if stage is None else [stage]

    if str(job.get("status", "")) == "queued":
        return ["convert", "upload", "parse"]

    artifact = source_row.get("artifact") if isinstance(source_row, Mapping) else None
    ragflow = source_row.get("ragflow") if isinstance(source_row, Mapping) else None
    source_state = str(source_row.get("state", "")) if isinstance(source_row, Mapping) else ""
    parse_status = str(ragflow.get("parse_status", "not_started")) if isinstance(ragflow, Mapping) else ""
    upload_status = (
        str(ragflow.get("upload_status", "not_uploaded")) if isinstance(ragflow, Mapping) else ""
    )

    if parse_status == "parsed" or source_state == "parsed":
        return []

    upload_complete = upload_status == "uploaded" or (
        not isinstance(ragflow, Mapping) and source_state in {"uploaded", "parsed"}
    )
    if upload_complete:
        return ["parse"]

    convert_complete = artifact is not None or source_state in {"converted", "uploaded", "parsed"}
    if convert_complete:
        return ["upload", "parse"]
    return ["convert", "upload", "parse"]


def _timing_source_row_for_job(
    job: Mapping[str, Any],
    source_row: Mapping[str, Any],
) -> Mapping[str, Any]:
    if str(job.get("kind", "")) not in {"sync_file", "convert"}:
        return source_row
    if str(job.get("status", "")) != "queued" and job.get("live_stage") != "convert":
        return source_row
    if str(source_row.get("extension", "")).lower() != "pdf":
        return source_row
    if str(source_row.get("profile_name", "")) not in {"quant-books", "quant-papers"}:
        return source_row
    updated = dict(source_row)
    updated.pop("artifact", None)
    return updated


def _remaining_from_live_progress(
    job: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
) -> int | None:
    if str(job.get("status", "")) != "running":
        return None
    progress = job.get("progress")
    if progress is None:
        return None
    started_at = _parse_timestamp(job.get("started_at"))
    current_time = _parse_timestamp(now)
    if started_at is None or current_time is None:
        return None
    elapsed_seconds = max(0.0, (current_time - started_at).total_seconds())
    return estimate_from_live_progress(elapsed_seconds=elapsed_seconds, progress=float(progress))


def _remaining_from_live_page_rate(
    db: Any,
    job: Mapping[str, Any],
    source_row: Mapping[str, Any],
    *,
    timing_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if str(job.get("status", "")) != "running":
        return None
    if job.get("live_stage") != "convert":
        return None
    if _parser_for_source(source_row) != "glm-ocr":
        return None
    pages_done = _positive_int(job.get("live_progress_pages_done"))
    page_count = _positive_int(job.get("live_progress_page_count") or source_row.get("page_count"))
    if pages_done is None or page_count is None:
        return None
    pages_remaining = max(0, page_count - pages_done)
    page_rate_estimate = _lookup_convert_page_rate_estimate(
        db,
        source_row,
        timing_context=timing_context,
    )
    if page_rate_estimate is None:
        return None
    total_seconds = page_rate_estimate["seconds"]
    if total_seconds is None:
        return None
    seconds_per_page = int(total_seconds) / page_count
    return {
        "seconds": max(0, int(round(seconds_per_page * pages_remaining))),
        "confidence": page_rate_estimate["confidence"],
        "timing_basis": f"{page_rate_estimate['timing_basis']}-live-pages",
    }


def _job_progress_percent(job: Mapping[str, Any]) -> int | None:
    live_percent = job.get("live_progress_percent")
    if live_percent is not None:
        try:
            return max(0, min(100, int(live_percent)))
        except (TypeError, ValueError):
            pass
    progress = job.get("progress")
    if progress is None:
        return None
    try:
        return max(0, min(100, int(round(float(progress) * 100))))
    except (TypeError, ValueError):
        return None


def _running_stage_estimate_without_progress(
    db: Any,
    job: Mapping[str, Any],
    source_row: Mapping[str, Any],
    stage: str,
    *,
    now: str | datetime | None = None,
    timing_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if str(job.get("status", "")) != "running":
        return None
    try:
        progress = float(job.get("progress", 0) or 0)
    except (TypeError, ValueError):
        progress = 0
    if progress > 0:
        return None
    started_at = _parse_timestamp(job.get("started_at"))
    current_time = _parse_timestamp(now)
    if started_at is None or current_time is None:
        return None
    estimate = _lookup_stage_estimate(db, source_row, stage, timing_context=timing_context)
    estimated_seconds = estimate["seconds"]
    if estimated_seconds is None:
        return None
    elapsed_seconds = max(0, int(round((current_time - started_at).total_seconds())))
    stall_threshold_seconds = max(int(estimated_seconds) * 2, int(estimated_seconds) + 300)
    if elapsed_seconds >= stall_threshold_seconds:
        return {
            "seconds": None,
            "confidence": "estimating",
            "timing_basis": "stalled",
            "progress_percent": None,
        }
    remaining_seconds = max(0, int(estimated_seconds) - elapsed_seconds)
    raw_progress_percent = max(1, int(round((elapsed_seconds / int(estimated_seconds)) * 100)))
    confidence = _degrade_confidence_for_source(str(estimate["confidence"]), source_row)
    progress_percent = min(_heuristic_progress_cap(confidence, source_row), raw_progress_percent)
    return {
        "seconds": remaining_seconds,
        "confidence": confidence,
        "timing_basis": f"{estimate['timing_basis']}-elapsed",
        "progress_percent": progress_percent,
    }


def _parse_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _parser_for_source(source_row: Mapping[str, Any]) -> str:
    artifact = source_row.get("artifact")
    if isinstance(artifact, Mapping):
        parser = artifact.get("parser")
        if parser:
            return str(parser)
    extension = str(source_row.get("extension", "")).lower()
    if extension in {"md", "markdown", "txt"}:
        return "passthrough"
    if (
        extension == "pdf"
        and str(source_row.get("profile_name", "")) in {"quant-books", "quant-papers"}
    ):
        return "glm-ocr"
    return "marker"


def _timing_keys(
    source_row: Mapping[str, Any],
    stage: str,
    *,
    parser: str | None = None,
) -> list[tuple[str, ...]]:
    source_type = str(source_row.get("source_type", ""))
    extension = str(source_row.get("extension", ""))
    size_band = size_band_for_bytes(int(source_row.get("size_bytes", 0) or 0))
    parser_name = parser or _parser_for_source(source_row)
    keys: list[tuple[str, ...]] = []
    if source_row.get("page_count") is not None:
        page_band = page_count_band(source_row.get("page_count"))
        keys.extend(
            [
                (stage, source_type, extension, page_band, size_band, parser_name),
                (stage, source_type, extension, page_band, parser_name),
            ]
        )
    keys.extend(
        [
        (stage, source_type, extension, size_band, parser_name),
        (stage, source_type, extension, size_band),
        (stage, source_type, extension),
        (stage, source_type),
        (stage,),
        ]
    )
    return keys


def _confidence(sample_size: int, fallback_depth: int) -> str:
    if sample_size >= 8 and fallback_depth == 0:
        return "high"
    if sample_size >= 3 and fallback_depth <= 2:
        return "medium"
    if sample_size >= 1:
        return "low"
    return "estimating"


def _combine_confidence(confidences: list[str]) -> str:
    if not confidences:
        return "estimating"
    ranking = {"estimating": 0, "low": 1, "medium": 2, "high": 3, "live": 4}
    return min(confidences, key=lambda value: ranking.get(value, 0))


def _history_rows(db: Any) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT
              pse.stage,
              pse.duration_seconds,
              pr.profile_name,
              pr.source_type,
              pr.parser,
              sf.extension,
              sf.size_bytes,
              sf.page_count,
              sf.pdf_producer
            FROM pipeline_stage_events AS pse
            LEFT JOIN pipeline_runs AS pr
              ON pr.id = pse.run_id
            LEFT JOIN source_files AS sf
              ON sf.id = pse.source_file_id
            WHERE pse.status = 'completed'
              AND pse.duration_seconds IS NOT NULL
            ORDER BY pse.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _baselines(db: Any) -> dict[tuple[str, ...], list[float]]:
    return _baselines_from_rows(_history_rows(db))


def _baselines_from_rows(rows: list[Mapping[str, Any]]) -> dict[tuple[str, ...], list[float]]:
    grouped: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for row in rows:
        duration = row.get("duration_seconds")
        if duration is None:
            continue
        for key in _timing_keys(row, str(row["stage"]), parser=str(row.get("parser") or "marker")):
            grouped[key].append(float(duration))
    return grouped


def _producer_class(source_row: Mapping[str, Any]) -> str:
    producer = str(source_row.get("pdf_producer", "") or "").lower()
    if "clearscan" in producer or "paper capture" in producer:
        return "clearscan"
    return "default"


def _page_rate_keys(source_row: Mapping[str, Any], stage: str, *, parser: str | None = None) -> list[tuple[str, ...]]:
    source_type = str(source_row.get("source_type", ""))
    extension = str(source_row.get("extension", ""))
    parser_name = parser or _parser_for_source(source_row)
    producer_class = _producer_class(source_row)
    return [
        (stage, source_type, extension, producer_class, parser_name),
        (stage, source_type, extension, parser_name),
        (stage, source_type, extension),
    ]


def _page_rate_baselines_from_rows(
    rows: list[Mapping[str, Any]],
) -> dict[tuple[str, ...], list[float]]:
    grouped: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for row in rows:
        if str(row.get("stage", "")) != "convert":
            continue
        if str(row.get("extension", "")).lower() != "pdf":
            continue
        page_count = row.get("page_count")
        duration = row.get("duration_seconds")
        if page_count is None or duration is None:
            continue
        try:
            pages = int(page_count)
        except (TypeError, ValueError):
            continue
        if pages <= 0:
            continue
        seconds_per_page = float(duration) / pages
        for key in _page_rate_keys(row, "convert", parser=str(row.get("parser") or "marker")):
            grouped[key].append(seconds_per_page)
    return grouped


def _lookup_stage_estimate(
    db: Any,
    source_row: Mapping[str, Any],
    stage: str,
    *,
    timing_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if stage == "convert" and str(source_row.get("extension", "")).lower() == "pdf":
        page_rate_estimate = _lookup_convert_page_rate_estimate(db, source_row, timing_context=timing_context)
        if page_rate_estimate is not None:
            return page_rate_estimate
    baselines = _baseline_map(db, timing_context)
    for fallback_depth, key in enumerate(_timing_keys(source_row, stage)):
        samples = baselines.get(key)
        if not samples:
            continue
        seconds = max(1, int(round(median(samples))))
        return {
            "seconds": seconds,
            "timing_basis": "+".join(key),
            "confidence": _degrade_confidence_for_source(
                _confidence(len(samples), fallback_depth),
                source_row,
            ),
        }
    return {
        "seconds": None,
        "timing_basis": stage,
        "confidence": "estimating",
    }


def _lookup_convert_page_rate_estimate(
    db: Any,
    source_row: Mapping[str, Any],
    *,
    timing_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    page_count = source_row.get("page_count")
    try:
        pages = int(page_count)
    except (TypeError, ValueError):
        return None
    if pages <= 0:
        return None

    page_rate_baselines = _page_rate_baseline_map(db, timing_context)
    for fallback_depth, key in enumerate(_page_rate_keys(source_row, "convert")):
        samples = page_rate_baselines.get(key)
        if not samples:
            continue
        seconds_per_page = median(samples)
        total_seconds = max(1, int(round(seconds_per_page * pages)))
        parser_label = str(key[-1]) if len(key) >= 4 and str(key[-1]) not in {"pdf", ""} else "generic"
        return {
            "seconds": total_seconds,
            "timing_basis": "+".join(
                ("convert", str(source_row.get("source_type", "")), "pdf", "page-rate", parser_label)
            ),
            "confidence": _degrade_confidence_for_source(_confidence(len(samples), fallback_depth), source_row),
        }
    return None


def _recent_throughput_seconds(db: Any) -> int | None:
    return _recent_throughput_seconds_from_rows(_history_rows(db))


def _recent_throughput_seconds_from_rows(rows: list[Mapping[str, Any]]) -> int | None:
    durations = [float(row["duration_seconds"]) for row in rows]
    if not durations:
        return None
    return max(1, int(round(median(durations))))


def _baseline_map(
    db: Any,
    timing_context: Mapping[str, Any] | None,
) -> dict[tuple[str, ...], list[float]]:
    if timing_context is not None:
        baselines = timing_context.get("baselines")
        if isinstance(baselines, dict):
            return baselines
    return _baselines(db)


def _page_rate_baseline_map(
    db: Any,
    timing_context: Mapping[str, Any] | None,
) -> dict[tuple[str, ...], list[float]]:
    if timing_context is not None:
        baselines = timing_context.get("page_rate_baselines")
        if isinstance(baselines, dict):
            return baselines
    return _page_rate_baselines_from_rows(_history_rows(db))


def _degrade_confidence_for_source(confidence: str, source_row: Mapping[str, Any]) -> str:
    if _producer_class(source_row) != "clearscan":
        return confidence
    if confidence == "high":
        return "medium"
    if confidence == "medium":
        return "low"
    return confidence


def _heuristic_progress_cap(confidence: str, source_row: Mapping[str, Any]) -> int:
    if _producer_class(source_row) == "clearscan":
        return 70
    if confidence == "high":
        return 90
    if confidence == "medium":
        return 85
    if confidence == "low":
        return 75
    return 60


def _throughput_seconds(db: Any, timing_context: Mapping[str, Any] | None) -> int | None:
    if timing_context is not None:
        throughput_seconds = timing_context.get("throughput_seconds")
        if throughput_seconds is None or isinstance(throughput_seconds, int):
            return throughput_seconds
    return _recent_throughput_seconds(db)


def _estimate_queue_glm_ocr_api_cost(
    db: Any,
    jobs: list[dict[str, Any]],
    files_by_id: dict[int, dict[str, Any]],
    *,
    timing_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    baseline = _glm_ocr_usage_baseline_from_context(db, timing_context)
    if baseline is None:
        return {}

    remaining_pages = 0
    for job in jobs:
        if str(job.get("status", "")) not in {"running", "queued"}:
            continue
        source_file_id = job.get("source_file_id")
        source_row = files_by_id.get(int(source_file_id)) if source_file_id is not None else None
        if source_row is None:
            continue
        timing_source_row = _timing_source_row_for_job(job, source_row)
        if _parser_for_source(timing_source_row) != "glm-ocr":
            continue
        if "convert" not in _remaining_stages_for_job(job, timing_source_row):
            continue
        page_count = _positive_int(timing_source_row.get("page_count"))
        if page_count is None:
            continue
        pages_done = 0
        if str(job.get("status", "")) == "running" and job.get("live_stage") == "convert":
            pages_done = _positive_int(job.get("live_progress_pages_done")) or 0
        remaining_pages += max(0, page_count - pages_done)

    if remaining_pages <= 0:
        return {}

    tokens = int(round(float(baseline["tokens_per_page"]) * remaining_pages))
    cost_usd = round(tokens * float(baseline["cost_per_token"]), 8)
    return {
        "estimated_api_tokens": tokens,
        "estimated_api_cost_usd": cost_usd,
        "estimated_api_cost_label": f"{_format_cost_usd(cost_usd)} estimated GLM OCR",
        "api_cost_basis": f"z-ai glm-ocr median {int(round(float(baseline['tokens_per_page'])))} tokens/page",
    }


def _glm_ocr_usage_baseline_from_context(
    db: Any,
    timing_context: Mapping[str, Any] | None,
) -> dict[str, float] | None:
    if timing_context is not None:
        baseline = timing_context.get("glm_ocr_usage_baseline")
        if baseline is None or isinstance(baseline, dict):
            return baseline
    return _glm_ocr_usage_baseline(db)


def _glm_ocr_usage_baseline(db: Any) -> dict[str, float] | None:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT
              ue.tokens,
              ue.cost_usd,
              COALESCE(
                CASE
                  WHEN json_valid(ue.metadata_json)
                  THEN json_extract(ue.metadata_json, '$.page_count')
                END,
                sf.page_count
              ) AS page_count
            FROM usage_events AS ue
            LEFT JOIN source_files AS sf
              ON sf.id = ue.source_file_id
            WHERE ue.provider = 'z-ai'
              AND ue.service = 'glm-ocr'
              AND ue.model = 'glm-ocr'
              AND ue.tokens > 0
            ORDER BY ue.id DESC
            """
        ).fetchall()

    tokens_per_page: list[float] = []
    total_tokens = 0
    total_cost = 0.0
    for row in rows:
        pages = _positive_int(row["page_count"])
        tokens = _positive_int(row["tokens"])
        if pages is None or tokens is None:
            continue
        tokens_per_page.append(tokens / pages)
        total_tokens += tokens
        total_cost += float(row["cost_usd"] or 0)
    if not tokens_per_page or total_tokens <= 0 or total_cost <= 0:
        return None
    return {
        "tokens_per_page": float(median(tokens_per_page)),
        "cost_per_token": total_cost / total_tokens,
    }


def _format_cost_usd(value: float) -> str:
    if value == 0:
        return "$0.00"
    if abs(value) < 0.01:
        return f"${value:.6f}"
    return f"${value:.2f}"


def _estimated_finish_at(
    seconds: int | None,
    *,
    now: str | datetime | None = None,
) -> str | None:
    if seconds is None:
        return None
    current_time = _parse_timestamp(now) or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    return (current_time + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _safe_stem(source_path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_path.stem)
    stem = re.sub(r"_+", "_", stem).strip("._-")
    return stem or "source"


def _live_glm_ocr_render_dir(
    source_row: Mapping[str, Any],
    *,
    output_root: Path,
) -> Path | None:
    stem = None
    artifact = source_row.get("artifact")
    if isinstance(artifact, Mapping) and artifact.get("output_path"):
        stem = Path(str(artifact["output_path"])).stem
        profile_root = Path(str(artifact["output_path"])).parent.parent
    else:
        source_path = Path(str(source_row.get("source_path", "")))
        if not source_path.name:
            return None
        path_hash = sha256(str(source_path).encode("utf-8")).hexdigest()[:12]
        stem = f"{_safe_stem(source_path)}-{path_hash}"
        profile_root = output_root / str(source_row.get("profile_name", ""))
    return profile_root / "glm-ocr" / ".parser-raw" / "glm-ocr" / stem / "rendered-pages"


def _unknown_queue_eta(throughput_seconds: int | None) -> dict[str, Any]:
    return {
        "seconds": None,
        "label": "unknown",
        "confidence": "estimating",
        "estimated_finish_at": None,
        "throughput_label": (
            "unknown throughput"
            if throughput_seconds is None
            else f"recent median {format_eta_seconds(throughput_seconds)}/file"
        ),
    }
