from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from rag_sync.config import DEFAULT_PROFILE_PATH, DEFAULT_RAGFLOW_BASE_URL, load_profiles
from rag_sync.db import RagSyncDb
from rag_sync.models import JobKind, Profile
from rag_sync.ragflow_client import PROTECTED_DATASETS, QUANT_DATASET_DEFAULTS
from rag_sync.sync import (
    convert_source_file,
    default_db,
    parse_uploaded_document,
    persist_scan,
    upload_latest_artifact,
)


class ConvertRequest(BaseModel):
    parser: str | None = None


class EnqueueJobRequest(BaseModel):
    kind: JobKind
    source_file_id: int | None = None
    profile_name: str | None = None


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
) -> FastAPI:
    app = FastAPI(title="RAG Sync", version="0.1.0")

    def load_configured_profiles() -> list[Profile]:
        try:
            return profile_loader(profile_path)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"failed to load profiles: {exc}",
            ) from exc

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
        ids = persist_scan(db_factory(), profile)
        return {"count": len(ids)}

    @app.get("/api/files")
    def files() -> dict[str, list[dict[str, object]]]:
        return {"files": db_factory().list_file_summaries()}

    @app.get("/api/files/{source_file_id}")
    def file_detail(source_file_id: int) -> dict[str, object]:
        db = db_factory()
        file_row = next(
            (row for row in db.list_file_summaries() if int(row["id"]) == source_file_id),
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
        return {"jobs": db_factory().list_jobs()}

    @app.get("/api/status")
    def status() -> dict[str, object]:
        counts = db_factory().job_counts()
        active = counts["running"]
        queued = counts["queued"]
        failed = counts["failed"]
        if active or queued:
            label = f"{active} active · {queued} queued"
        elif failed:
            label = f"{failed} failed"
        else:
            label = "Idle"
        return {"queue": counts, "label": label}

    @app.post("/api/jobs")
    def enqueue_job(request: EnqueueJobRequest) -> dict[str, int]:
        job_id = db_factory().create_job(
            request.kind.value,
            source_file_id=request.source_file_id,
            profile_name=request.profile_name,
        )
        return {"job_id": job_id}

    @app.post("/api/files/{source_file_id}/convert")
    def convert_file(
        source_file_id: int,
        request: ConvertRequest | None = None,
    ) -> dict[str, str]:
        try:
            output_path = convert_source_file(
                db_factory(),
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
                        db_factory(),
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
            return dict(asyncio.run(parse_uploaded_document(db_factory(), source_file_id)))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/files/{source_file_id}/ragflow/stop")
    def enqueue_stop_ragflow(source_file_id: int) -> dict[str, int]:
        job_id = db_factory().create_job(
            JobKind.STOP_RAGFLOW.value,
            source_file_id=source_file_id,
        )
        return {"job_id": job_id}

    @app.delete("/api/files/{source_file_id}/ragflow")
    def enqueue_delete_ragflow(source_file_id: int) -> dict[str, int]:
        job_id = db_factory().create_job(
            JobKind.DELETE_RAGFLOW.value,
            source_file_id=source_file_id,
        )
        return {"job_id": job_id}

    @app.post("/api/files/{source_file_id}/ragflow/restart")
    def enqueue_restart_ragflow(source_file_id: int) -> dict[str, int]:
        job_id = db_factory().create_job(
            JobKind.RESTART_RAGFLOW.value,
            source_file_id=source_file_id,
        )
        return {"job_id": job_id}

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
