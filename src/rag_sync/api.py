from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI

from rag_sync.config import DEFAULT_PROFILE_PATH, load_profiles


def create_app() -> FastAPI:
    app = FastAPI(title="RAG Sync", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/profiles")
    def profiles() -> dict[str, list[dict[str, object]]]:
        loaded = []
        if DEFAULT_PROFILE_PATH.exists():
            for profile in load_profiles(DEFAULT_PROFILE_PATH):
                row = asdict(profile)
                row["source_paths"] = [str(path) for path in profile.source_paths]
                row["output_dir"] = str(profile.output_dir) if profile.output_dir else None
                loaded.append(row)
        return {"profiles": loaded}

    return app


app = create_app()
