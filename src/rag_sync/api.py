from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from rag_sync.config import DEFAULT_PROFILE_PATH, DEFAULT_RAGFLOW_BASE_URL, load_profiles
from rag_sync.db import RagSyncDb
from rag_sync.import_manifest import import_manifest_batch, preview_manifest_batch
from rag_sync.ldd import log_event
from rag_sync.history import (
    apply_live_glm_ocr_progress,
    estimate_job_timing,
    estimate_queue_timing,
    format_eta_seconds,
    prepare_timing_context,
)
from rag_sync.models import JobKind, Profile
from rag_sync.parsers import terminate_active_parser_processes
from rag_sync.queue import JobCanceledError, PersistentJobQueue
from rag_sync.ragflow_client import PROTECTED_DATASETS, QUANT_DATASET_DEFAULTS, RagFlowClient
from rag_sync.scanner import backfill_pdf_metadata
from rag_sync.sync import (
    convert_source_file,
    default_db,
    delete_ragflow_document,
    parse_uploaded_document,
    persist_scan,
    refresh_ragflow_documents,
    restart_ragflow_document,
    upload_latest_artifact,
)

logger = logging.getLogger(__name__)
OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
OPENROUTER_USAGE_CACHE_TTL_SECONDS = 60
_openrouter_usage_cache: tuple[float, dict[str, object]] | None = None


class ConvertRequest(BaseModel):
    parser: str | None = None


class EnqueueJobRequest(BaseModel):
    kind: JobKind
    source_file_id: int | None = None
    profile_name: str | None = None


class FileFilterRequest(BaseModel):
    query: str = ""
    profile: str = ""
    sourceType: str = ""
    state: str = ""
    parser: str = ""
    ragflow: str = ""


class BulkEnqueueJobRequest(BaseModel):
    kind: JobKind
    source_file_ids: list[int] = []
    filters: FileFilterRequest | None = None


class ImportBatchPreviewRequest(BaseModel):
    batch_dir: str
    selected_relpaths: list[str] | None = None


class ImportBatchRequest(BaseModel):
    batch_dir: str
    force: bool = False
    reason: str = ""
    selected_relpaths: list[str] | None = None


def format_file_name(source_path: str) -> str:
    return Path(source_path).name


def infer_job_stage(
    job: dict[str, object],
    file_row: dict[str, object] | None,
) -> dict[str, object]:
    status = str(job.get("status", "queued"))
    kind = str(job.get("kind", ""))
    artifact = file_row.get("artifact") if isinstance(file_row, dict) else None
    ragflow = file_row.get("ragflow") if isinstance(file_row, dict) else None
    parser_name = (
        str(artifact.get("parser", "marker"))
        if isinstance(artifact, dict) and artifact.get("parser")
        else "marker"
    )
    parser_label = "Marker conversion" if parser_name == "marker" else f"{parser_name} conversion"
    if parser_name == "glm-ocr":
        parser_label = "GLM OCR conversion"
    artifact_created_at = (
        str(artifact.get("created_at", ""))
        if isinstance(artifact, dict) and artifact.get("created_at")
        else ""
    )
    job_started_at = str(job.get("started_at") or "")
    artifact_is_stale_for_job = (
        bool(artifact_created_at)
        and bool(job_started_at)
        and artifact_created_at < job_started_at
    )

    if status == "queued":
        return {"key": "queued", "label": "Queued", "status": "queued", "progress": 0.0}

    if job.get("live_stage") == "convert":
        return {
            "key": "convert",
            "label": "GLM OCR conversion",
            "status": status,
            "progress": float(job.get("progress", 0) or 0),
            "detail": str(job.get("live_progress_detail", "")),
        }

    if kind in {JobKind.CONVERT.value, JobKind.SYNC_FILE.value} and not artifact:
        return {
            "key": "convert",
            "label": parser_label,
            "status": status,
            "progress": float(job.get("progress", 0) or 0),
        }
    if (
        status == "failed"
        and kind in {JobKind.CONVERT.value, JobKind.SYNC_FILE.value}
        and (not artifact or artifact_is_stale_for_job)
    ):
        return {
            "key": "convert",
            "label": parser_label,
            "status": status,
            "progress": float(job.get("progress", 0) or 0),
        }
    if kind in {JobKind.UPLOAD.value, JobKind.SYNC_FILE.value} and artifact and not ragflow:
        return {"key": "upload", "label": "Upload to RAGFlow", "status": status, "progress": 0.72}
    if kind in {JobKind.PARSE.value, JobKind.SYNC_FILE.value} and isinstance(ragflow, dict):
        parse_status = str(ragflow.get("parse_status", "not_started"))
        if parse_status != "parsed":
            return {
                "key": "parse",
                "label": "RAGFlow parsing",
                "status": "running" if status == "running" else parse_status,
                "progress": max(
                    float(job.get("progress", 0) or 0),
                    0.9 if status == "completed" else 0.82,
                ),
            }

    return {
        "key": "done",
        "label": "Done",
        "status": status,
        "progress": float(job.get("progress", 0) or 1),
    }


def enrich_jobs(
    db: RagSyncDb,
    jobs: list[dict[str, object]],
    files: list[dict[str, object]],
    runtime_active_job: dict[str, object] | None = None,
    timing_context: dict[str, object] | None = None,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    job_rows = [dict(job) for job in jobs]
    runtime_active_id = (
        int(runtime_active_job["id"])
        if isinstance(runtime_active_job, dict) and runtime_active_job.get("id") is not None
        else None
    )
    if runtime_active_id is not None:
        matched = False
        for job in job_rows:
            if int(job["id"]) != runtime_active_id:
                continue
            job["status"] = "running"
            matched = True
            break
        if not matched:
            active_copy = dict(runtime_active_job)
            active_copy["status"] = "running"
            job_rows.append(active_copy)

    files_by_id = {int(row["id"]): row for row in files}
    queued_positions = {
        int(job["id"]): index + 1
        for index, job in enumerate(
            sorted(
                (
                    job
                    for job in job_rows
                    if str(job.get("status")) == "queued"
                    and (runtime_active_id is None or int(job["id"]) != runtime_active_id)
                ),
                key=lambda row: int(row["id"]),
            )
        )
    }
    enriched: list[dict[str, object]] = []
    for job in job_rows:
        source_file_id = job.get("source_file_id")
        file_row = files_by_id.get(int(source_file_id)) if source_file_id is not None else None
        source_path = str(file_row.get("source_path", "")) if isinstance(file_row, dict) else ""
        item = dict(job)
        item["source_path"] = source_path
        item["file_name"] = format_file_name(source_path) if source_path else ""
        item["source_type"] = (
            str(file_row.get("source_type", "")) if isinstance(file_row, dict) else ""
        )
        if isinstance(file_row, dict):
            item = apply_live_glm_ocr_progress(item, file_row)
        item["queue_position"] = (
            0 if str(job.get("status")) != "queued" else queued_positions.get(int(job["id"]), 0)
        )
        item["stage"] = infer_job_stage(item, file_row)
        enriched.append(item)
    status_rank = {"running": 0, "queued": 1, "failed": 2, "completed": 3, "canceled": 4}

    def job_sort_key(job: dict[str, object]) -> tuple[object, ...]:
        status = str(job.get("status", "queued"))
        rank = status_rank.get(status, 9)
        if status == "queued":
            queue_position = int(job.get("queue_position", 0) or 0)
            return (rank, queue_position, int(job["id"]))
        if status == "running":
            started_at = str(job.get("started_at") or "")
            return (rank, started_at, int(job["id"]))
        finished_at = str(job.get("finished_at") or "")
        return (rank, f"~{finished_at}", -int(job["id"]))

    sorted_jobs = sorted(enriched, key=job_sort_key)
    wait_seconds_total = 0
    wait_is_known = True
    for job in sorted_jobs:
        status = str(job.get("status", ""))
        if status not in {"running", "queued"}:
            job["eta_seconds"] = None
            job["eta_label"] = "unknown"
            job["wait_seconds"] = None
            job["wait_label"] = "unknown"
            job["confidence"] = "estimating"
            job["timing_basis"] = "unknown"
            continue

        if status == "running":
            job["wait_seconds"] = 0
            job["wait_label"] = format_eta_seconds(0)
        else:
            wait_seconds = wait_seconds_total if wait_is_known else None
            job["wait_seconds"] = wait_seconds
            job["wait_label"] = format_eta_seconds(wait_seconds)

        source_file_id = job.get("source_file_id")
        source_row = files_by_id.get(int(source_file_id)) if source_file_id is not None else None
        if source_row is None:
            estimate = {
                "eta_seconds": None,
                "eta_label": "unknown",
                "confidence": "estimating",
                "timing_basis": "unknown",
            }
        else:
            estimate = estimate_job_timing(
                db,
                job,
                source_row,
                now=now,
                timing_context=timing_context,
            )
        job.update(estimate)

        eta_seconds = estimate["eta_seconds"]
        if eta_seconds is None:
            wait_is_known = False
        else:
            wait_seconds_total += int(eta_seconds)

    return sorted_jobs


def current_job_rows(jobs: list[dict[str, object]]) -> list[dict[str, object]]:
    active_or_queued: list[dict[str, object]] = []
    latest_terminal_by_source: dict[tuple[object, object], dict[str, object]] = {}
    latest_terminal_without_source: dict[tuple[object, object], dict[str, object]] = {}
    active_sources: set[object] = set()

    for job in jobs:
        status = str(job.get("status", ""))
        source_file_id = job.get("source_file_id")
        if status in {"queued", "running"}:
            active_or_queued.append(job)
            if source_file_id is not None:
                active_sources.add(source_file_id)
            continue
        if status not in {"completed", "failed", "canceled"}:
            continue
        if source_file_id is not None:
            key = ("source", source_file_id)
            existing = latest_terminal_by_source.get(key)
            if existing is None or int(job["id"]) > int(existing["id"]):
                latest_terminal_by_source[key] = job
            continue
        key = (job.get("kind"), job.get("profile_name"))
        existing = latest_terminal_without_source.get(key)
        if existing is None or int(job["id"]) > int(existing["id"]):
            latest_terminal_without_source[key] = job

    terminal_jobs = [
        job
        for (_, source_file_id), job in latest_terminal_by_source.items()
        if source_file_id not in active_sources
    ]
    terminal_jobs.extend(latest_terminal_without_source.values())
    return active_or_queued + terminal_jobs


def hide_resolved_failed_jobs(
    jobs: list[dict[str, object]],
    files_by_id: dict[int, dict[str, object]],
) -> list[dict[str, object]]:
    visible: list[dict[str, object]] = []
    for job in jobs:
        if str(job.get("status")) not in {"failed", "canceled"}:
            visible.append(job)
            continue
        source_file_id = job.get("source_file_id")
        source_row = files_by_id.get(int(source_file_id)) if source_file_id is not None else None
        if source_row is not None and str(source_row.get("state")) == "parsed":
            continue
        visible.append(job)
    return visible


def runtime_active_job_payload(
    queue: PersistentJobQueue | None,
    jobs: list[dict[str, object]],
) -> dict[str, object] | None:
    if queue is None or queue.current_job is None or queue.current_job_id is None:
        return None
    runtime_job = dict(queue.current_job)
    runtime_job["id"] = int(queue.current_job_id)
    runtime_job["status"] = "running"
    persisted = next(
        (job for job in jobs if int(job["id"]) == int(queue.current_job_id)),
        None,
    )
    if persisted is not None:
        merged = dict(persisted)
        merged.update(runtime_job)
        merged["status"] = "running"
        return merged
    return runtime_job


def _read_meminfo() -> tuple[int, int] | None:
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw_value = line.split(":", 1)
            values[key] = int(raw_value.strip().split()[0])
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if total is None or available is None or total <= 0:
            return None
        used = max(0, total - available)
        return used, total
    except Exception:
        return None


def read_system_metrics() -> dict[str, dict[str, object]]:
    cpu_count = max(os.cpu_count() or 1, 1)
    load1 = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
    cpu_value = int(round(min(100.0, (load1 / cpu_count) * 100)))
    metrics: dict[str, dict[str, object]] = {
        "cpu": {
            "label": f"CPU {cpu_value}% · load {load1:.1f}",
            "value": cpu_value,
            "detail": f"{load1:.1f}/{cpu_count}",
        }
    }
    mem = _read_meminfo()
    if mem is not None:
        used_kib, total_kib = mem
        memory_value = int(round((used_kib / total_kib) * 100))
        metrics["memory"] = {
            "label": f"RAM {memory_value}%",
            "value": memory_value,
            "detail": f"{used_kib // 1024 // 1024:.1f} / {total_kib // 1024 // 1024:.1f} GiB",
        }
    else:
        metrics["memory"] = {"label": "RAM unavailable", "value": None, "detail": ""}
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,name",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            first = proc.stdout.strip().splitlines()[0]
            gpu_util, memory_used, memory_total, name = [
                part.strip() for part in first.split(",", 3)
            ]
            metrics["gpu"] = {
                "label": f"GPU {gpu_util}% · VRAM {memory_used}/{memory_total} MiB",
                "value": int(gpu_util),
                "detail": name,
            }
        else:
            metrics["gpu"] = {"label": "GPU unavailable", "value": None, "detail": ""}
    except Exception:
        metrics["gpu"] = {"label": "GPU unavailable", "value": None, "detail": ""}
    return metrics


def read_openrouter_api_key(env_file: Path = Path(".env")) -> str | None:
    value = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if value:
        return value
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key.strip() == "OPENROUTER_API_KEY":
            return raw_value.strip().strip('"').strip("'") or None
    return None


def openrouter_account_usage(now: float | None = None) -> dict[str, object]:
    global _openrouter_usage_cache
    current_time = time.monotonic() if now is None else now
    if (
        _openrouter_usage_cache is not None
        and current_time - _openrouter_usage_cache[0] < OPENROUTER_USAGE_CACHE_TTL_SECONDS
    ):
        return dict(_openrouter_usage_cache[1])

    key = read_openrouter_api_key()
    if not key:
        return {
            "tracked": False,
            "provider": "openrouter",
            "label": "OpenRouter account",
            "tokens": 0,
            "calls": 0,
            "cost_usd": 0,
            "note": "OPENROUTER_API_KEY is not set.",
        }
    try:
        response = httpx.get(
            OPENROUTER_CREDITS_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            data = {}
        total_credits = float(data.get("total_credits") or 0)
        total_usage = float(data.get("total_usage") or 0)
        usage = {
            "tracked": True,
            "provider": "openrouter",
            "label": "OpenRouter account",
            "tokens": 0,
            "calls": 0,
            "cost_usd": round(total_usage, 8),
            "total_credits": round(total_credits, 8),
            "total_usage": round(total_usage, 8),
            "remaining_credits": round(max(0.0, total_credits - total_usage), 8),
            "note": "Account-level usage from OpenRouter credits API.",
        }
        _openrouter_usage_cache = (current_time, usage)
        log_event(
            "openrouter.usage.fetched",
            "ok",
            total_credits=usage["total_credits"],
            total_usage=usage["total_usage"],
            remaining_credits=usage["remaining_credits"],
        )
        return dict(usage)
    except Exception as exc:
        log_event(
            "openrouter.usage.fetch_failed",
            "error",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return {
            "tracked": False,
            "provider": "openrouter",
            "label": "OpenRouter account",
            "tokens": 0,
            "calls": 0,
            "cost_usd": 0,
            "note": f"OpenRouter credits unavailable: {type(exc).__name__}",
        }


def usage_summary_with_openrouter(db: RagSyncDb) -> dict[str, object]:
    summary = db.usage_summary()
    providers = dict(summary["providers"])
    openrouter = openrouter_account_usage()
    previous_openrouter = providers.get("openrouter")
    if (
        isinstance(previous_openrouter, dict)
        and not bool(openrouter.get("tracked"))
        and int(previous_openrouter.get("tokens", 0) or 0) > 0
    ):
        openrouter = {**openrouter, **previous_openrouter}
    providers["openrouter"] = openrouter
    total_cost = sum(
        float(provider.get("cost_usd", 0) or 0)
        for provider in providers.values()
        if isinstance(provider, dict) and bool(provider.get("tracked"))
    )
    summary["providers"] = providers
    summary["total_cost_usd"] = round(total_cost, 8)
    return summary


def matches_file_filters(file_row: dict[str, object], filters: FileFilterRequest) -> bool:
    normalized = filters.query.strip().lower()
    artifact = file_row.get("artifact")
    ragflow = file_row.get("ragflow")
    parser = (
        str(artifact.get("parser", ""))
        if isinstance(artifact, dict)
        else ""
    )
    ragflow_status = (
        str(ragflow.get("parse_status", "not_uploaded"))
        if isinstance(ragflow, dict)
        else "not_uploaded"
    )
    haystack = " ".join(
        [
            str(file_row.get("source_path", "")),
            str(file_row.get("profile_name", "")),
            str(file_row.get("source_type", "")),
            str(file_row.get("extension", "")),
            str(file_row.get("state", "")),
            str(file_row.get("tags", "")),
            parser,
            ragflow_status,
        ]
    ).lower()
    if normalized and normalized not in haystack:
        return False
    if filters.profile and str(file_row.get("profile_name", "")) != filters.profile:
        return False
    if filters.sourceType and str(file_row.get("source_type", "")) != filters.sourceType:
        return False
    if filters.state and str(file_row.get("state", "")) != filters.state:
        return False
    if filters.parser and parser != filters.parser:
        return False
    return not filters.ragflow or ragflow_status == filters.ragflow


def serialize_profile(profile: Profile) -> dict[str, object]:
    return {
        "name": profile.name,
        "source_paths": [str(path) for path in profile.source_paths],
        "file_types": list(profile.file_types),
        "parser_mode": profile.parser_mode.value,
        "target_dataset": profile.target_dataset,
        "source_type": profile.source_type,
        "enabled": profile.enabled,
        "output_dir": str(profile.output_dir) if profile.output_dir else None,
        "skip_rules": {
            "path_parts": list(profile.skip_rules.path_parts),
            "suffixes": list(profile.skip_rules.suffixes),
        },
        "max_convert_workers": profile.max_convert_workers,
        "max_upload_workers": profile.max_upload_workers,
        "max_parse_workers": profile.max_parse_workers,
    }


DATASET_DRIFT_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("chunk_method", "Chunk method", ("chunk_method",)),
    ("chunk_token_num", "Chunk tokens", ("parser_config", "chunk_token_num")),
    ("auto_keywords", "Auto keywords", ("parser_config", "auto_keywords")),
    ("auto_questions", "Auto questions", ("parser_config", "auto_questions")),
    ("toc_extraction", "TOC extraction", ("parser_config", "ext", "toc_extraction")),
    ("use_parent_child", "Parent-child", ("parser_config", "parent_child", "use_parent_child")),
)


def dataset_nested_value(payload: dict[str, object], path: tuple[str, ...]) -> object | None:
    current: object = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def build_dataset_drift(
    expected: dict[str, object],
    actual: dict[str, object],
) -> list[dict[str, object]]:
    drift: list[dict[str, object]] = []
    for field, label, path in DATASET_DRIFT_FIELDS:
        actual_value = dataset_nested_value(actual, path)
        if actual_value is None:
            continue
        expected_value = dataset_nested_value(expected, path)
        if expected_value == actual_value:
            continue
        drift.append(
            {
                "field": field,
                "label": label,
                "expected": expected_value,
                "actual": actual_value,
            }
        )
    return drift


def build_dataset_coverage(files: list[dict[str, object]]) -> dict[str, int]:
    coverage = {
        "file_count": len(files),
        "indexed_documents": 0,
        "parsed_documents": 0,
        "stuck_documents": 0,
        "failed_documents": 0,
        "chunk_count": 0,
    }
    for file_row in files:
        ragflow = file_row.get("ragflow")
        parse_status = ""
        if isinstance(ragflow, dict):
            coverage["indexed_documents"] += 1
            parse_status = str(ragflow.get("parse_status") or "")
            coverage["chunk_count"] += int(ragflow.get("chunk_count") or 0)
        if parse_status == "parsed":
            coverage["parsed_documents"] += 1
        elif parse_status in {"parsing", "not_started"}:
            coverage["stuck_documents"] += 1
        if str(file_row.get("state") or "") == "failed" or parse_status in {
            "failed",
            "error",
            "canceled",
        }:
            coverage["failed_documents"] += 1
    return coverage


def file_dataset_name(
    file_row: dict[str, object],
    profiles_by_name: dict[str, Profile],
) -> str:
    ragflow = file_row.get("ragflow")
    if isinstance(ragflow, dict) and ragflow.get("dataset_name"):
        return str(ragflow["dataset_name"])
    profile_name = str(file_row.get("profile_name") or "")
    profile = profiles_by_name.get(profile_name)
    return profile.target_dataset if profile is not None else ""


def create_app(
    profile_path: Path = DEFAULT_PROFILE_PATH,
    profile_loader: Callable[[Path], list[Profile]] = load_profiles,
    db_factory: Callable[[], RagSyncDb] = default_db,
    worker_poll_interval: float = 0.5,
    worker_enabled: bool = True,
) -> FastAPI:
    db = db_factory()

    async def worker_loop(queue: PersistentJobQueue, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                ran_job = await queue.run_next()
            except Exception:
                logger.exception("RAG Sync worker iteration failed; continuing")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=worker_poll_interval)
                except TimeoutError:
                    continue
                continue
            if ran_job:
                continue
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=worker_poll_interval)
            except TimeoutError:
                continue

    def ensure_worker_task_running(app_instance: FastAPI) -> bool:
        if not worker_enabled:
            return False
        if not bool(getattr(app_instance.state, "worker_lock_acquired", False)):
            return False
        queue = getattr(app_instance.state, "queue", None)
        if queue is None:
            return False
        existing_task = getattr(app_instance.state, "worker_task", None)
        if existing_task is not None and not existing_task.done():
            return False
        stop_event = getattr(app_instance.state, "worker_stop_event", None)
        if stop_event is None or stop_event.is_set():
            stop_event = asyncio.Event()
            app_instance.state.worker_stop_event = stop_event
        app_instance.state.worker_task = asyncio.create_task(worker_loop(queue, stop_event))
        return True

    def load_configured_profiles() -> list[Profile]:
        try:
            return profile_loader(profile_path)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"failed to load profiles: {exc}",
            ) from exc

    def configured_profile_names() -> set[str] | None:
        if not profile_path.exists():
            return None
        try:
            return {profile.name for profile in load_configured_profiles()}
        except HTTPException:
            return None

    async def stop_ragflow_document(source_file_id: int) -> dict[str, object]:
        with db.session() as conn:
            row = conn.execute(
                "SELECT * FROM ragflow_documents WHERE source_file_id = ?",
                (source_file_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"No RAGFlow document found for source file {source_file_id}")
        client = RagFlowClient()
        dataset_id = str(row["dataset_id"])
        document_id = str(row["document_id"])
        response = await client.stop_documents(dataset_id, [document_id])
        with db.session() as conn:
            conn.execute(
                """
                UPDATE ragflow_documents
                SET parse_status = ?, last_synced_at = CURRENT_TIMESTAMP
                WHERE source_file_id = ?
                """,
                ("stopped", source_file_id),
            )
        return response

    def register_queue_handlers(queue: PersistentJobQueue) -> None:
        configured_profiles_cache: dict[str, Profile] | None = None

        def configured_profiles_by_name() -> dict[str, Profile]:
            nonlocal configured_profiles_cache
            if configured_profiles_cache is None:
                configured_profiles_cache = {
                    profile.name: profile for profile in load_configured_profiles()
                }
            return configured_profiles_cache

        def stage_snapshot(source_file_id: int, parser_name: str) -> str:
            source_row = next(
                (
                    row
                    for row in db.list_source_files()
                    if int(row["id"]) == source_file_id
                ),
                None,
            )
            if source_row is None:
                return "{}"
            return json.dumps(
                {
                    "profile_name": str(source_row.get("profile_name", "")),
                    "source_type": str(source_row.get("source_type", "")),
                    "extension": str(source_row.get("extension", "")),
                    "size_bytes": int(source_row.get("size_bytes", 0) or 0),
                    "page_count": source_row.get("page_count"),
                    "pdf_producer": str(source_row.get("pdf_producer", "")),
                    "parser": parser_name,
                }
            )

        def latest_artifact_parser(source_file_id: int, fallback: str) -> str:
            artifact = db.latest_artifact_for_source(source_file_id)
            if artifact is not None and artifact.get("parser"):
                return str(artifact["parser"])
            return fallback

        def latest_artifact(source_file_id: int) -> dict[str, object] | None:
            artifact = db.latest_artifact_for_source(source_file_id)
            return dict(artifact) if artifact is not None else None

        def parser_name_for_job(job: dict[str, object]) -> str:
            source_file_id = int(job["source_file_id"])
            kind = str(job.get("kind") or "")
            source_row = next(
                (
                    row
                    for row in db.list_source_files()
                    if int(row["id"]) == source_file_id
                ),
                None,
            )
            extension = str(source_row.get("extension", "")).lower() if source_row else ""
            if extension == "md":
                return "passthrough"
            if kind in {JobKind.SYNC_FILE.value, JobKind.CONVERT.value}:
                profile_name = str(source_row.get("profile_name") or "")
                profile = configured_profiles_by_name().get(profile_name)
                if profile is not None:
                    return profile.parser_mode.value
                return "marker"
            profile_name = str(job.get("profile_name") or source_row.get("profile_name") or "")
            profile = configured_profiles_by_name().get(profile_name)
            artifact = db.latest_artifact_for_source(source_file_id)
            if artifact is not None and artifact.get("parser"):
                return str(artifact["parser"])
            if profile is not None:
                return profile.parser_mode.value
            return "marker"

        def job_canceled(job_id: int) -> bool:
            return job_id in queue.cancel_requested_job_ids

        async def run_tracked_stage(
            *,
            run_id: int,
            job: dict[str, object],
            stage: str,
            parser_name: str,
            operation: Callable[[], object | asyncio.Future],
        ) -> tuple[object, str]:
            source_file_id = int(job["source_file_id"])
            artifact_before = latest_artifact(source_file_id)
            started = time.monotonic()
            try:
                result = operation()
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception as exc:
                canceled = job_canceled(int(job["id"]))
                db.record_stage_event(
                    run_id=run_id,
                    job_id=int(job["id"]),
                    source_file_id=source_file_id,
                    stage=stage,
                    status="canceled" if canceled else "failed",
                    progress=0.0,
                    progress_message=f"{stage} canceled" if canceled else f"{stage} failed",
                    duration_seconds=time.monotonic() - started,
                    error_summary="killed by user" if canceled else str(exc),
                    data_json=stage_snapshot(source_file_id, parser_name),
                )
                raise
            artifact_after = latest_artifact(source_file_id)
            actual_parser_name = parser_name
            if artifact_after is not None:
                before_id = artifact_before.get("id") if artifact_before is not None else None
                after_id = artifact_after.get("id")
                if before_id != after_id and artifact_after.get("parser"):
                    actual_parser_name = str(artifact_after["parser"])
            db.update_pipeline_run_parser(run_id, actual_parser_name)
            db.record_stage_event(
                run_id=run_id,
                job_id=int(job["id"]),
                source_file_id=source_file_id,
                stage=stage,
                status="completed",
                progress=1.0,
                progress_message=f"{stage} completed",
                duration_seconds=time.monotonic() - started,
                error_summary="",
                data_json=stage_snapshot(source_file_id, actual_parser_name),
            )
            return result, actual_parser_name

        async def run_pipeline_job(
            job: dict[str, object],
            stages: list[tuple[str, Callable[[], object | asyncio.Future]]],
        ) -> dict[str, object]:
            source_file_id = int(job["source_file_id"])
            parser_name = parser_name_for_job(job)
            source_row = next(
                (
                    row
                    for row in db.list_source_files()
                    if int(row["id"]) == source_file_id
                ),
                None,
            )
            run_id = db.create_pipeline_run(
                source_file_id,
                (
                    str(source_row.get("profile_name") or "")
                    if str(job.get("kind") or "") in {JobKind.SYNC_FILE.value, JobKind.CONVERT.value}
                    else str(job.get("profile_name") or source_row.get("profile_name") or "")
                ),
                str(source_row.get("source_type", "")) if source_row is not None else "",
                parser_name,
                str(job["kind"]),
            )
            current_parser_name = parser_name
            try:
                for stage_name, operation in stages:
                    _, current_parser_name = await run_tracked_stage(
                        run_id=run_id,
                        job=job,
                        stage=stage_name,
                        parser_name=current_parser_name,
                        operation=operation,
                    )
                    if job_canceled(int(job["id"])):
                        raise JobCanceledError("killed by user")
            except Exception as exc:
                if job_canceled(int(job["id"])):
                    db.finish_pipeline_run(run_id, "canceled", error_summary="killed by user")
                else:
                    db.finish_pipeline_run(run_id, "failed", error_summary=str(exc))
                raise
            if job_canceled(int(job["id"])):
                db.finish_pipeline_run(run_id, "canceled", error_summary="killed by user")
                raise JobCanceledError("killed by user")
            db.finish_pipeline_run(run_id, "completed")
            return {"ok": True}

        async def handle_sync_file(job: dict[str, object]) -> dict[str, object] | None:
            source_file_id = int(job["source_file_id"])
            return await run_pipeline_job(
                job,
                [
                    (
                        "convert",
                        lambda: asyncio.to_thread(
                            convert_source_file,
                            db,
                            source_file_id,
                            None,
                            profile_path,
                        ),
                    ),
                    (
                        "upload",
                        lambda: upload_latest_artifact(
                            db,
                            source_file_id,
                            profile_path=profile_path,
                        ),
                    ),
                    (
                        "parse",
                        lambda: parse_uploaded_document(db, source_file_id),
                    ),
                ],
            )

        async def handle_convert(job: dict[str, object]) -> dict[str, object] | None:
            source_file_id = int(job["source_file_id"])
            return await run_pipeline_job(
                job,
                [
                    (
                        "convert",
                        lambda: asyncio.to_thread(
                            convert_source_file,
                            db,
                            source_file_id,
                            None,
                            profile_path,
                        ),
                    )
                ],
            )

        async def handle_upload(job: dict[str, object]) -> dict[str, object] | None:
            source_file_id = int(job["source_file_id"])
            return await run_pipeline_job(
                job,
                [
                    (
                        "upload",
                        lambda: upload_latest_artifact(
                            db,
                            source_file_id,
                            profile_path=profile_path,
                        ),
                    )
                ],
            )

        async def handle_parse(job: dict[str, object]) -> dict[str, object] | None:
            source_file_id = int(job["source_file_id"])
            return await run_pipeline_job(
                job,
                [("parse", lambda: parse_uploaded_document(db, source_file_id))],
            )

        async def handle_restart(job: dict[str, object]) -> dict[str, object] | None:
            source_file_id = int(job["source_file_id"])
            await restart_ragflow_document(db, source_file_id, profile_path=profile_path)
            return {"ok": True}

        async def handle_delete(job: dict[str, object]) -> dict[str, object] | None:
            source_file_id = int(job["source_file_id"])
            await delete_ragflow_document(db, source_file_id)
            return {"ok": True}

        async def handle_stop(job: dict[str, object]) -> dict[str, object] | None:
            source_file_id = int(job["source_file_id"])
            await stop_ragflow_document(source_file_id)
            return {"ok": True}

        queue.register(JobKind.SYNC_FILE, handle_sync_file)
        queue.register(JobKind.CONVERT, handle_convert)
        queue.register(JobKind.UPLOAD, handle_upload)
        queue.register(JobKind.PARSE, handle_parse)
        queue.register(JobKind.RESTART_RAGFLOW, handle_restart)
        queue.register(JobKind.DELETE_RAGFLOW, handle_delete)
        queue.register(JobKind.STOP_RAGFLOW, handle_stop)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        queue = PersistentJobQueue(db)
        register_queue_handlers(queue)
        _app.state.queue = queue
        _app.state.profile_names = configured_profile_names()
        worker_owner = f"pid-{os.getpid()}-app-{id(_app)}"
        _app.state.worker_lock_acquired = False
        _app.state.worker_task = None
        _app.state.worker_stop_event = None
        log_event(
            "app.lifecycle.started",
            "ok",
            pid=os.getpid(),
            worker_enabled=worker_enabled,
            worker_owner=worker_owner,
            profile_count=len(_app.state.profile_names),
        )

        if db.acquire_worker_lock(worker_owner):
            _app.state.worker_lock_acquired = True
            backfill_pdf_metadata(db, profile_names=_app.state.profile_names)
            db.requeue_running_jobs()
            if _app.state.profile_names:
                db.cancel_jobs_for_missing_profiles(_app.state.profile_names)

        if not worker_enabled:
            try:
                yield
            finally:
                if _app.state.worker_lock_acquired:
                    db.release_worker_lock(worker_owner)
            return

        stop_event = asyncio.Event()
        _app.state.worker_stop_event = stop_event
        if _app.state.worker_lock_acquired:
            ensure_worker_task_running(_app)
        try:
            yield
        finally:
            log_event(
                "app.lifecycle.stopping",
                "ok",
                pid=os.getpid(),
                worker_enabled=worker_enabled,
                worker_owner=worker_owner,
                worker_lock_acquired=bool(_app.state.worker_lock_acquired),
                active_job_id=getattr(queue, "current_job_id", None),
                queue_paused=bool(queue.paused),
            )
            stop_event = getattr(_app.state, "worker_stop_event", None)
            if stop_event is not None:
                stop_event.set()
            task = getattr(_app.state, "worker_task", None)
            if task is not None:
                await task
            if _app.state.worker_lock_acquired:
                db.release_worker_lock(worker_owner)
            log_event(
                "app.lifecycle.stopped",
                "ok",
                pid=os.getpid(),
                worker_enabled=worker_enabled,
                worker_owner=worker_owner,
            )

    app = FastAPI(title="RAG Sync", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/profiles")
    def profiles() -> dict[str, list[dict[str, object]]]:
        if not profile_path.exists():
            return {"profiles": []}
        loaded = [serialize_profile(profile) for profile in load_configured_profiles()]
        return {"profiles": loaded}

    @app.get("/api/settings")
    def settings() -> dict[str, object]:
        profiles_payload: list[dict[str, object]]
        if profile_path.exists():
            profiles_payload = [
                serialize_profile(profile) for profile in load_configured_profiles()
            ]
        else:
            profiles_payload = []
        return {
            "profile_path": str(profile_path),
            "ragflow_base_url": DEFAULT_RAGFLOW_BASE_URL,
            "protected_datasets": sorted(PROTECTED_DATASETS),
            "dataset_defaults": QUANT_DATASET_DEFAULTS,
            "profiles": profiles_payload,
            "usage": usage_summary_with_openrouter(db),
        }

    @app.get("/api/datasets")
    def datasets() -> dict[str, object]:
        profiles = load_configured_profiles() if profile_path.exists() else []
        profiles_by_name = {profile.name: profile for profile in profiles}
        files = db.list_file_summaries(
            profile_names={profile.name for profile in profiles} if profiles else None
        )
        remote_error: str | None = None
        remote_by_name: dict[str, dict[str, object]] = {}
        try:
            remote_datasets = asyncio.run(RagFlowClient().list_datasets())
            remote_by_name = {
                str(dataset.get("name") or ""): dataset
                for dataset in remote_datasets
                if str(dataset.get("name") or "")
            }
            log_event(
                "datasets.overview.fetched",
                "ok",
                configured_count=len(profiles),
                local_file_count=len(files),
                remote_dataset_count=len(remote_by_name),
            )
        except Exception as exc:
            remote_error = f"RAGFlow dataset metadata unavailable: {type(exc).__name__}: {exc}"
            log_event(
                "datasets.overview.fetch_failed",
                "error",
                configured_count=len(profiles),
                local_file_count=len(files),
                error_type=type(exc).__name__,
                error=str(exc),
            )

        dataset_names = sorted(
            {
                *remote_by_name.keys(),
                *(profile.target_dataset for profile in profiles),
            }
        )
        datasets_payload: list[dict[str, object]] = []
        for dataset_name in dataset_names:
            expected = QUANT_DATASET_DEFAULTS.get(dataset_name, {})
            remote = remote_by_name.get(dataset_name)
            dataset_files = [
                file_row
                for file_row in files
                if file_dataset_name(file_row, profiles_by_name) == dataset_name
            ]
            dataset_profiles = [
                {
                    "name": profile.name,
                    "parser_mode": profile.parser_mode.value,
                    "source_type": profile.source_type,
                    "source_paths": [str(path) for path in profile.source_paths],
                    "file_count": sum(
                        1
                        for file_row in dataset_files
                        if str(file_row.get("profile_name") or "") == profile.name
                    ),
                }
                for profile in profiles
                if profile.target_dataset == dataset_name
            ]
            coverage = build_dataset_coverage(dataset_files)
            datasets_payload.append(
                {
                    "name": dataset_name,
                    "exists": remote is not None,
                    "protected": dataset_name in PROTECTED_DATASETS,
                    "coverage": coverage,
                    "profiles": dataset_profiles,
                    "drift": build_dataset_drift(expected, remote or {}),
                    "remote": (
                        {
                            "id": str(remote.get("id") or ""),
                            "document_count": remote.get("document_count")
                            or remote.get("doc_num")
                            or coverage["indexed_documents"],
                        }
                        if isinstance(remote, dict)
                        else None
                    ),
                }
            )
        return {"datasets": datasets_payload, "remote_error": remote_error}

    @app.post("/api/scan/{profile_name}")
    def scan(profile_name: str) -> dict[str, int]:
        profiles_by_name = {
            profile.name: profile for profile in load_configured_profiles()
        }
        profile = profiles_by_name.get(profile_name)
        if profile is None:
            raise HTTPException(
                status_code=404,
                detail=f"unknown profile: {profile_name}",
            )
        ids = persist_scan(db, profile)
        return {"count": len(ids)}

    @app.get("/api/files")
    def files() -> dict[str, list[dict[str, object]]]:
        profile_names = getattr(app.state, "profile_names", configured_profile_names())
        asyncio.run(refresh_ragflow_documents(db))
        return {"files": db.list_file_summaries(profile_names=profile_names)}

    @app.get("/api/files/{source_file_id}")
    def file_detail(source_file_id: int) -> dict[str, object]:
        asyncio.run(refresh_ragflow_documents(db))
        file_row = next(
            (
                row
                for row in db.list_file_summaries(
                    profile_names=getattr(app.state, "profile_names", configured_profile_names())
                )
                if int(row["id"]) == source_file_id
            ),
            None,
        )
        if file_row is None:
            raise HTTPException(status_code=404, detail="source file not found")
        return {
            "file": file_row,
            "history": db.recent_stage_events(source_file_id),
        }

    @app.get("/api/jobs")
    def jobs() -> dict[str, object]:
        profile_names = getattr(app.state, "profile_names", configured_profile_names())
        files = db.list_file_summaries(profile_names=profile_names)
        job_rows = current_job_rows(db.list_jobs())
        queue = getattr(app.state, "queue", None)
        timing_context = prepare_timing_context(db)
        current_time = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
        files_by_id = {int(row["id"]): row for row in files}
        enriched = enrich_jobs(
            db,
            job_rows,
            files,
            runtime_active_job=runtime_active_job_payload(queue, job_rows),
            timing_context=timing_context,
            now=current_time,
        )
        return {
            "jobs": hide_resolved_failed_jobs(enriched, files_by_id)
        }

    @app.get("/api/status")
    def status() -> dict[str, object]:
        queue = getattr(app.state, "queue", None)
        profile_names = getattr(app.state, "profile_names", configured_profile_names())
        job_rows = current_job_rows(db.list_jobs())
        files = db.list_file_summaries(profile_names=profile_names)
        timing_context = prepare_timing_context(db)
        current_time = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
        jobs = enrich_jobs(
            db,
            job_rows,
            files,
            runtime_active_job=runtime_active_job_payload(queue, job_rows),
            timing_context=timing_context,
            now=current_time,
        )
        files_by_id = {int(row["id"]): row for row in files}
        jobs = hide_resolved_failed_jobs(jobs, files_by_id)
        active_job = next((job for job in jobs if str(job.get("status")) == "running"), None)
        counts = {"queued": 0, "running": 0, "failed": 0, "completed": 0}
        for job in jobs:
            status = str(job.get("status", ""))
            if status in counts:
                counts[status] += 1
        active = counts["running"]
        queued = counts["queued"]
        failed = counts["failed"]
        if active or queued:
            label = f"{active} active · {queued} queued"
        elif failed:
            label = f"{failed} failed"
        else:
            label = "Idle"
        return {
            "queue": {**counts, "paused": bool(queue.paused) if queue is not None else False},
            "queue_eta": estimate_queue_timing(
                db,
                jobs,
                files_by_id,
                now=current_time,
                timing_context=timing_context,
            ),
            "label": label,
            "active": active_job,
            "system": read_system_metrics(),
            "usage": usage_summary_with_openrouter(db),
        }

    @app.post("/api/queue/pause")
    def pause_queue() -> dict[str, bool]:
        queue = getattr(app.state, "queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="queue unavailable")
        queue.paused = True
        log_event(
            "queue.paused",
            "ok",
            pid=os.getpid(),
            active_job_id=getattr(queue, "current_job_id", None),
        )
        return {"paused": True}

    @app.post("/api/queue/resume")
    async def resume_queue() -> dict[str, bool]:
        queue = getattr(app.state, "queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="queue unavailable")
        queue.paused = False
        ensure_worker_task_running(app)
        log_event(
            "queue.resumed",
            "ok",
            pid=os.getpid(),
            active_job_id=getattr(queue, "current_job_id", None),
        )
        return {"paused": False}

    @app.post("/api/queue/kill")
    def kill_queue() -> dict[str, object]:
        queue = getattr(app.state, "queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="queue unavailable")
        queue.paused = True
        canceled = queue.request_cancel_running()
        terminated = terminate_active_parser_processes()
        log_event(
            "queue.kill_requested",
            "ok",
            pid=os.getpid(),
            active_job_id=getattr(queue, "current_job_id", None),
            canceled_running_job=bool(canceled),
            terminated_processes=terminated,
        )
        return {
            "paused": True,
            "canceled_running_job": canceled,
            "terminated_processes": terminated,
        }

    @app.post("/api/jobs")
    def enqueue_job(request: EnqueueJobRequest) -> dict[str, int]:
        job_id = db.create_job(
            request.kind.value,
            source_file_id=request.source_file_id,
            profile_name=request.profile_name,
        )
        return {"job_id": job_id}

    @app.post("/api/jobs/bulk")
    def bulk_enqueue_jobs(request: BulkEnqueueJobRequest) -> dict[str, object]:
        source_file_ids = [int(source_file_id) for source_file_id in request.source_file_ids]
        if request.kind == JobKind.SYNC_FILTERED:
            if request.filters is None:
                raise HTTPException(
                    status_code=400,
                    detail="filters are required for sync_filtered bulk requests",
                )
            matched_files = [
                row
                for row in db.list_file_summaries(
                    profile_names=getattr(app.state, "profile_names", configured_profile_names())
                )
                if matches_file_filters(row, request.filters)
            ]
            source_file_ids = [int(row["id"]) for row in matched_files]
            job_kind = JobKind.SYNC_FILE.value
        else:
            job_kind = request.kind.value
        if not source_file_ids:
            return {"count": 0, "job_ids": [], "source_file_ids": []}

        source_files_by_id = {
            int(row["id"]): row
            for row in db.list_source_files(
                profile_names=getattr(app.state, "profile_names", configured_profile_names())
            )
        }
        job_ids: list[int] = []
        queued_source_file_ids: list[int] = []
        for source_file_id in source_file_ids:
            file_row = source_files_by_id.get(source_file_id)
            if file_row is None:
                continue
            job_ids.append(
                db.create_job(
                    kind=job_kind,
                    source_file_id=source_file_id,
                    profile_name=str(file_row["profile_name"]),
                )
            )
            queued_source_file_ids.append(source_file_id)
        return {
            "count": len(job_ids),
            "job_ids": job_ids,
            "source_file_ids": queued_source_file_ids,
        }

    @app.post("/api/files/{source_file_id}/convert")
    def convert_file(
        source_file_id: int,
        request: ConvertRequest | None = None,
    ) -> dict[str, str]:
        try:
            output_path = convert_source_file(
                db,
                source_file_id,
                request.parser if request else None,
                profile_path,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"output_path": str(output_path)}

    @app.post("/api/files/{source_file_id}/upload")
    def upload_file(source_file_id: int) -> dict[str, object]:
        try:
            return dict(
                asyncio.run(
                    upload_latest_artifact(
                        db,
                        source_file_id,
                        profile_path=profile_path,
                    )
                )
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/files/{source_file_id}/parse")
    def parse_file(source_file_id: int) -> dict[str, object]:
        try:
            return dict(asyncio.run(parse_uploaded_document(db, source_file_id)))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/files/{source_file_id}/ragflow/stop")
    def enqueue_stop_ragflow(source_file_id: int) -> dict[str, int]:
        job_id = db.create_job(
            JobKind.STOP_RAGFLOW.value,
            source_file_id=source_file_id,
        )
        return {"job_id": job_id}

    @app.delete("/api/files/{source_file_id}/ragflow")
    def enqueue_delete_ragflow(source_file_id: int) -> dict[str, int]:
        job_id = db.create_job(
            JobKind.DELETE_RAGFLOW.value,
            source_file_id=source_file_id,
        )
        return {"job_id": job_id}

    @app.post("/api/files/{source_file_id}/ragflow/restart")
    def enqueue_restart_ragflow(source_file_id: int) -> dict[str, int]:
        job_id = db.create_job(
            JobKind.RESTART_RAGFLOW.value,
            source_file_id=source_file_id,
        )
        return {"job_id": job_id}

    @app.post("/api/import-batches/preview")
    def preview_import_batch_endpoint(request: ImportBatchPreviewRequest) -> dict[str, object]:
        try:
            return preview_manifest_batch(
                db,
                Path(request.batch_dir),
                selected_relpaths=request.selected_relpaths,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/import-batches/import")
    def import_batch_endpoint(request: ImportBatchRequest) -> dict[str, object]:
        if request.force and not request.reason.strip():
            raise HTTPException(
                status_code=400,
                detail="force import requires a non-empty reason",
            )
        try:
            return import_manifest_batch(
                db,
                Path(request.batch_dir),
                force=request.force,
                reason=request.reason,
                selected_relpaths=request.selected_relpaths,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/retrieval/query-sets/{name}")
    def retrieval_query_set(name: str) -> dict[str, object]:
        from rag_sync.retrieval import query_set

        try:
            queries = query_set(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown query set: {name}") from exc
        return {
            "name": name,
            "queries": [
                {"id": query_id, "question": question}
                for query_id, question in queries
            ],
        }

    return app


app = create_app()
