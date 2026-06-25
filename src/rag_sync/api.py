from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException

from rag_sync.config import DEFAULT_PROFILE_PATH, load_profiles
from rag_sync.db import RagSyncDb
from rag_sync.models import Profile
from rag_sync.sync import default_db, persist_scan


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
        return {"files": db_factory().list_source_files()}

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
