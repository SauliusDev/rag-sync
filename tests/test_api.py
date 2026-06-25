from pathlib import Path

from fastapi.testclient import TestClient

import rag_sync.api
from rag_sync.api import create_app
from rag_sync.db import RagSyncDb
from rag_sync.models import SourceState


def test_health_endpoint():
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_profiles_endpoint_serializes_profile_config(tmp_path: Path):
    config = tmp_path / "profiles.toml"
    config.write_text(
        """
[[profiles]]
name = "quant-articles"
source_paths = ["/atlas/articles"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-articles"
source_type = "article"
enabled = true
output_dir = "/tmp/rag-sync/articles"
max_convert_workers = 1
max_upload_workers = 4
max_parse_workers = 2

[profiles.skip_rules]
path_parts = ["_meta"]
suffixes = [".excalidraw.md"]
""",
        encoding="utf-8",
    )
    client = TestClient(create_app(profile_path=config))

    response = client.get("/api/profiles")

    assert response.status_code == 200
    profiles = response.json()["profiles"]
    assert profiles == [
        {
            "name": "quant-articles",
            "source_paths": ["/atlas/articles"],
            "file_types": ["md"],
            "parser_mode": "passthrough",
            "target_dataset": "quant-articles",
            "source_type": "article",
            "enabled": True,
            "output_dir": "/tmp/rag-sync/articles",
            "skip_rules": {
                "path_parts": ["_meta"],
                "suffixes": [".excalidraw.md"],
            },
            "max_convert_workers": 1,
            "max_upload_workers": 4,
            "max_parse_workers": 2,
        }
    ]


def test_profiles_endpoint_returns_empty_list_for_missing_config(tmp_path: Path):
    client = TestClient(create_app(profile_path=tmp_path / "missing.toml"))

    response = client.get("/api/profiles")

    assert response.status_code == 200
    assert response.json() == {"profiles": []}


def test_profiles_endpoint_returns_stable_error_for_bad_config(tmp_path: Path):
    config = tmp_path / "profiles.toml"
    config.write_text("profiles = []", encoding="utf-8")
    client = TestClient(create_app(profile_path=config))

    response = client.get("/api/profiles")

    assert response.status_code == 500
    assert response.json()["detail"].startswith("failed to load profiles:")


def test_settings_endpoint_returns_runtime_and_dataset_defaults(tmp_path: Path):
    config = tmp_path / "profiles.toml"
    config.write_text(
        """
[[profiles]]
name = "quant-books"
source_paths = ["/atlas/books"]
file_types = ["pdf"]
parser_mode = "marker"
target_dataset = "quant-books"
source_type = "book"
""",
        encoding="utf-8",
    )
    client = TestClient(create_app(profile_path=config))

    response = client.get("/api/settings")

    assert response.status_code == 200
    data = response.json()
    assert data["profile_path"] == str(config)
    assert data["ragflow_base_url"] == "http://127.0.0.1:9380"
    assert data["protected_datasets"] == ["quant-books-legacy"]
    assert data["dataset_defaults"]["quant-books"]["chunk_method"] == "naive"
    assert data["dataset_defaults"]["quant-books"]["parser_config"]["auto_keywords"] == 0
    assert data["dataset_defaults"]["quant-books"]["parser_config"]["auto_questions"] == 0
    assert data["dataset_defaults"]["quant-books"]["parser_config"]["chunk_token_num"] == 1000
    assert data["profiles"][0]["name"] == "quant-books"


def test_scan_endpoint_returns_404_for_unknown_profile(tmp_path: Path):
    config = tmp_path / "profiles.toml"
    config.write_text(
        """
[[profiles]]
name = "quant-articles"
source_paths = ["/atlas/articles"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-articles"
source_type = "article"
""",
        encoding="utf-8",
    )
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    client = TestClient(create_app(profile_path=config, db_factory=lambda: db))

    response = client.post("/api/scan/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "unknown profile: missing"}


def test_scan_and_files_endpoints_use_injected_db(tmp_path: Path):
    source_dir = tmp_path / "articles"
    source_dir.mkdir()
    (source_dir / "example.md").write_text("# Example\n", encoding="utf-8")
    config = tmp_path / "profiles.toml"
    config.write_text(
        f"""
[[profiles]]
name = "quant-articles"
source_paths = ["{source_dir}"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-articles"
source_type = "article"
""",
        encoding="utf-8",
    )
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    client = TestClient(create_app(profile_path=config, db_factory=lambda: db))

    scan_response = client.post("/api/scan/quant-articles")
    files_response = client.get("/api/files")

    assert scan_response.status_code == 200
    assert scan_response.json() == {"count": 1}
    assert files_response.status_code == 200
    files = files_response.json()["files"]
    assert len(files) == 1
    assert files[0]["profile_name"] == "quant-articles"
    assert files[0]["source_path"].endswith("example.md")


def test_retrieval_query_set_endpoint_returns_formula_benchmark():
    client = TestClient(create_app())

    response = client.get("/api/retrieval/query-sets/formula-benchmark")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "formula-benchmark"
    assert len(data["queries"]) == 10
    assert data["queries"][0]["id"] == "Q1"
    assert "fractional differentiation" in data["queries"][0]["question"]


def test_convert_file_endpoint_runs_conversion_with_injected_db(
    monkeypatch,
    tmp_path: Path,
):
    config = tmp_path / "profiles.toml"
    config.write_text("", encoding="utf-8")
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    calls = []

    def fake_convert(actual_db, source_file_id, parser_name=None, profile_path=config):
        calls.append((actual_db, source_file_id, parser_name, profile_path))
        return tmp_path / "output.md"

    monkeypatch.setattr(rag_sync.api, "convert_source_file", fake_convert)
    client = TestClient(create_app(profile_path=config, db_factory=lambda: db))

    response = client.post("/api/files/42/convert", json={"parser": "marker"})

    assert response.status_code == 200
    assert response.json() == {"output_path": str(tmp_path / "output.md")}
    assert calls == [(db, 42, "marker", config)]


def test_upload_file_endpoint_returns_ragflow_document(
    monkeypatch,
    tmp_path: Path,
):
    config = tmp_path / "profiles.toml"
    config.write_text("", encoding="utf-8")
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    calls = []

    async def fake_upload(actual_db, source_file_id, profile_path=config):
        calls.append((actual_db, source_file_id, profile_path))
        return {"dataset_id": "dataset", "document_id": "doc", "document_name": "out.md"}

    monkeypatch.setattr(rag_sync.api, "upload_latest_artifact", fake_upload)
    client = TestClient(create_app(profile_path=config, db_factory=lambda: db))

    response = client.post("/api/files/42/upload")

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "dataset",
        "document_id": "doc",
        "document_name": "out.md",
    }
    assert calls == [(db, 42, config)]


def test_parse_file_endpoint_returns_parse_response(
    monkeypatch,
    tmp_path: Path,
):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    calls = []

    async def fake_parse(actual_db, source_file_id):
        calls.append((actual_db, source_file_id))
        return {"code": 0, "message": "ok"}

    monkeypatch.setattr(rag_sync.api, "parse_uploaded_document", fake_parse)
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.post("/api/files/42/parse")

    assert response.status_code == 200
    assert response.json() == {"code": 0, "message": "ok"}
    assert calls == [(db, 42)]


def test_jobs_endpoint_lists_persisted_jobs(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    job_id = db.create_job("convert", source_file_id=None, profile_name="quant-books")
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.get("/api/jobs")

    assert response.status_code == 200
    assert response.json()["jobs"][0]["id"] == job_id
    assert response.json()["jobs"][0]["kind"] == "convert"


def test_status_endpoint_returns_queue_counts(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    db.create_job("convert", source_file_id=None, profile_name="quant-books")
    running_id = db.create_job("parse", source_file_id=None, profile_name="quant-books")
    db.update_job_status(running_id, "running", progress=0.4)
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["queue"] == {
        "queued": 1,
        "running": 1,
        "failed": 0,
        "completed": 0,
    }
    assert "label" in response.json()


def test_enqueue_job_endpoint_creates_job(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.post(
        "/api/jobs",
        json={"kind": "sync_file", "source_file_id": None, "profile_name": "quant-books"},
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == 1


def test_ragflow_action_endpoints_enqueue_jobs(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source = tmp_path / "example.md"
    source.write_text("# Example\n", encoding="utf-8")
    source_id = db.upsert_source_file(
        profile_name="quant-articles",
        source_path=str(source),
        source_type="article",
        extension="md",
        sha256="abc",
        size_bytes=source.stat().st_size,
        mtime=source.stat().st_mtime,
        state=SourceState.NEW,
    )
    client = TestClient(create_app(db_factory=lambda: db))

    stop_response = client.post(f"/api/files/{source_id}/ragflow/stop")
    restart_response = client.post(f"/api/files/{source_id}/ragflow/restart")
    delete_response = client.delete(f"/api/files/{source_id}/ragflow")

    assert stop_response.status_code == 200
    assert restart_response.status_code == 200
    assert delete_response.status_code == 200
    with db.connect() as conn:
        kinds = [
            row["kind"]
            for row in conn.execute("SELECT kind FROM jobs ORDER BY id").fetchall()
        ]
    assert kinds == ["stop_ragflow", "restart_ragflow", "delete_ragflow"]
