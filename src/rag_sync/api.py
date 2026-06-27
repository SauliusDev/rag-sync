from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from rag_sync.config import DEFAULT_PROFILE_PATH, DEFAULT_RAGFLOW_BASE_URL, load_profiles
from rag_sync.db import RagSyncDb
from rag_sync.import_manifest import import_manifest_batch, preview_manifest_batch
from rag_sync.models import JobKind, Profile
from rag_sync.parsers import terminate_active_parser_processes
from rag_sync.queue import PersistentJobQueue
from rag_sync.ragflow_client import PROTECTED_DATASETS, QUANT_DATASET_DEFAULTS, RagFlowClient
from rag_sync.sync import (
    convert_source_file,
    default_db,
    delete_ragflow_document,
    parse_uploaded_document,
    persist_scan,
    restart_ragflow_document,
    upload_latest_artifact,
)


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
    selected_relpaths: list[str] = []


class ImportBatchRequest(BaseModel):
    batch_dir: str
    force: bool = False
    reason: str = ""
    selected_relpaths: list[str] = []


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

    if status == "queued":
        return {"key": "queued", "label": "Queued", "status": "queued", "progress": 0.0}

    if kind in {JobKind.CONVERT.value, JobKind.SYNC_FILE.value} and not artifact:
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
    jobs: list[dict[str, object]],
    files: list[dict[str, object]],
) -> list[dict[str, object]]:
    files_by_id = {int(row["id"]): row for row in files}
    queued_positions = {
        int(job["id"]): index + 1
        for index, job in enumerate(
            sorted(
                (job for job in jobs if str(job.get("status")) == "queued"),
                key=lambda row: int(row["id"]),
            )
        )
    }
    enriched: list[dict[str, object]] = []
    for job in jobs:
        source_file_id = job.get("source_file_id")
        file_row = files_by_id.get(int(source_file_id)) if source_file_id is not None else None
        source_path = str(file_row.get("source_path", "")) if isinstance(file_row, dict) else ""
        item = dict(job)
        item["source_path"] = source_path
        item["file_name"] = format_file_name(source_path) if source_path else ""
        item["source_type"] = (
            str(file_row.get("source_type", "")) if isinstance(file_row, dict) else ""
        )
        item["queue_position"] = (
            0 if str(job.get("status")) != "queued" else queued_positions.get(int(job["id"]), 0)
        )
        item["stage"] = infer_job_stage(job, file_row)
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

    return sorted(enriched, key=job_sort_key)


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


def create_app(
    profile_path: Path = DEFAULT_PROFILE_PATH,
    profile_loader: Callable[[Path], list[Profile]] = load_profiles,
    db_factory: Callable[[], RagSyncDb] = default_db,
    worker_poll_interval: float = 0.5,
    worker_enabled: bool = True,
) -> FastAPI:
    db = db_factory()

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
        async def handle_sync_file(job: dict[str, object]) -> dict[str, object] | None:
            source_file_id = int(job["source_file_id"])
            await asyncio.to_thread(
                convert_source_file,
                db,
                source_file_id,
                None,
                profile_path,
            )
            await upload_latest_artifact(db, source_file_id, profile_path=profile_path)
            await parse_uploaded_document(db, source_file_id)
            return {"ok": True}

        async def handle_convert(job: dict[str, object]) -> dict[str, object] | None:
            source_file_id = int(job["source_file_id"])
            await asyncio.to_thread(
                convert_source_file,
                db,
                source_file_id,
                None,
                profile_path,
            )
            return {"ok": True}

        async def handle_upload(job: dict[str, object]) -> dict[str, object] | None:
            source_file_id = int(job["source_file_id"])
            await upload_latest_artifact(db, source_file_id, profile_path=profile_path)
            return {"ok": True}

        async def handle_parse(job: dict[str, object]) -> dict[str, object] | None:
            source_file_id = int(job["source_file_id"])
            await parse_uploaded_document(db, source_file_id)
            return {"ok": True}

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
        db.requeue_running_jobs()
        if _app.state.profile_names:
            db.cancel_jobs_for_missing_profiles(_app.state.profile_names)

        if not worker_enabled:
            yield
            return

        stop_event = asyncio.Event()

        async def worker_loop() -> None:
            while not stop_event.is_set():
                ran_job = await queue.run_next()
                if ran_job:
                    continue
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=worker_poll_interval)
                except TimeoutError:
                    continue

        task = asyncio.create_task(worker_loop())
        _app.state.worker_task = task
        _app.state.worker_stop_event = stop_event
        try:
            yield
        finally:
            stop_event.set()
            await task

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
        }

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
        return {"files": db.list_file_summaries(profile_names=profile_names)}

    @app.get("/api/files/{source_file_id}")
    def file_detail(source_file_id: int) -> dict[str, object]:
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
        return {"jobs": enrich_jobs(db.list_jobs(), files)}

    @app.get("/api/status")
    def status() -> dict[str, object]:
        counts = db.job_counts()
        queue = getattr(app.state, "queue", None)
        active = counts["running"]
        queued = counts["queued"]
        failed = counts["failed"]
        if active or queued:
            label = f"{active} active · {queued} queued"
        elif failed:
            label = f"{failed} failed"
        else:
            label = "Idle"
        profile_names = getattr(app.state, "profile_names", configured_profile_names())
        jobs = enrich_jobs(
            db.list_jobs(),
            db.list_file_summaries(profile_names=profile_names),
        )
        active_job = next((job for job in jobs if str(job.get("status")) == "running"), None)
        return {
            "queue": {**counts, "paused": bool(queue.paused) if queue is not None else False},
            "label": label,
            "active": active_job,
            "system": read_system_metrics(),
        }

    @app.post("/api/queue/pause")
    def pause_queue() -> dict[str, bool]:
        queue = getattr(app.state, "queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="queue unavailable")
        queue.paused = True
        return {"paused": True}

    @app.post("/api/queue/resume")
    def resume_queue() -> dict[str, bool]:
        queue = getattr(app.state, "queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="queue unavailable")
        queue.paused = False
        return {"paused": False}

    @app.post("/api/queue/kill")
    def kill_queue() -> dict[str, object]:
        queue = getattr(app.state, "queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="queue unavailable")
        queue.paused = True
        canceled = queue.request_cancel_running()
        terminated = terminate_active_parser_processes()
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
        return preview_manifest_batch(
            db,
            Path(request.batch_dir),
            selected_relpaths=request.selected_relpaths,
        )

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
