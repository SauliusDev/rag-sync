from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException

from rag_sync.config import DEFAULT_PROFILE_PATH, load_profiles
from rag_sync.models import Profile


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
) -> FastAPI:
    app = FastAPI(title="RAG Sync", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/profiles")
    def profiles() -> dict[str, list[dict[str, object]]]:
        if not profile_path.exists():
            return {"profiles": []}
        try:
            loaded = [serialize_profile(profile) for profile in profile_loader(profile_path)]
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"failed to load profiles: {exc}",
            ) from exc
        return {"profiles": loaded}

    return app


app = create_app()
