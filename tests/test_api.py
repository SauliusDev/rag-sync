import time
import json
from pathlib import Path

from fastapi.testclient import TestClient

import rag_sync.api
from rag_sync.api import create_app
from rag_sync.db import RagSyncDb
from rag_sync.models import SourceState


def make_import_batch_dir(tmp_path: Path, *, source_relpath: str, source_sha256: str) -> Path:
    batch_dir = tmp_path / "batch"
    markdown_relpath = Path("outputs") / Path(source_relpath).with_suffix(".md")
    markdown_path = batch_dir / markdown_relpath
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("# Imported\n", encoding="utf-8")
    manifest_path = batch_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "batch_id": "batch-1",
                "created_at": "2026-06-26T12:00:00+00:00",
                "host": "cluster-a",
                "profile": "quant-books",
                "parser": "marker",
                "parser_version": "1.10.2",
                "parser_flags": ["--workers", "1"],
                "files": [
                    {
                        "source_relpath": source_relpath,
                        "source_filename": Path(source_relpath).name,
                        "source_abspath_cluster": f"/cluster/input/{source_relpath}",
                        "source_sha256": source_sha256,
                        "source_size_bytes": 123,
                        "source_mtime": 1.0,
                        "page_count": 10,
                        "markdown_relpath": str(markdown_relpath),
                        "markdown_sha256": "markdown-hash",
                        "markdown_size_bytes": markdown_path.stat().st_size,
                        "status": "ok",
                        "started_at": "2026-06-26T12:00:00+00:00",
                        "finished_at": "2026-06-26T12:00:01+00:00",
                        "duration_seconds": 1.0,
                        "returncode": 0,
                        "error_type": None,
                        "error_message": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return batch_dir


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
        "paused": False,
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


def test_background_worker_processes_sync_file_jobs(
    monkeypatch,
    tmp_path: Path,
):
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
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source = tmp_path / "book.pdf"
    source.write_text("pdf", encoding="utf-8")
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="pdf",
        sha256="abc",
        size_bytes=source.stat().st_size,
        mtime=source.stat().st_mtime,
        state=SourceState.NEW,
    )
    calls: list[tuple[str, int]] = []

    def fake_convert(actual_db, source_file_id, parser_name=None, profile_path=config):
        calls.append(("convert", source_file_id))
        return tmp_path / "book.md"

    async def fake_upload(actual_db, source_file_id, client=None, profile_path=config):
        calls.append(("upload", source_file_id))
        return {"dataset_id": "dataset", "document_id": "doc", "document_name": "book.md"}

    async def fake_parse(actual_db, source_file_id, client=None):
        calls.append(("parse", source_file_id))
        return {"code": 0}

    monkeypatch.setattr(rag_sync.api, "convert_source_file", fake_convert)
    monkeypatch.setattr(rag_sync.api, "upload_latest_artifact", fake_upload)
    monkeypatch.setattr(rag_sync.api, "parse_uploaded_document", fake_parse)

    with TestClient(
        create_app(
            profile_path=config,
            db_factory=lambda: db,
            worker_poll_interval=0.01,
        )
    ) as client:
        response = client.post(
            "/api/jobs",
            json={
                "kind": "sync_file",
                "source_file_id": source_id,
                "profile_name": "quant-books",
            },
        )
        assert response.status_code == 200
        for _ in range(50):
            job = db.list_jobs(limit=1)[0]
            if job["status"] == "completed":
                break
            time.sleep(0.02)

    job = db.list_jobs(limit=1)[0]
    assert job["status"] == "completed"
    assert calls == [("convert", source_id), ("upload", source_id), ("parse", source_id)]


def test_background_worker_does_not_block_api_while_conversion_runs(
    monkeypatch,
    tmp_path: Path,
):
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
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source = tmp_path / "book.pdf"
    source.write_text("pdf", encoding="utf-8")
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="pdf",
        sha256="abc",
        size_bytes=source.stat().st_size,
        mtime=source.stat().st_mtime,
        state=SourceState.NEW,
    )

    def fake_convert(actual_db, source_file_id, parser_name=None, profile_path=config):
        time.sleep(0.3)
        return tmp_path / "book.md"

    async def fake_upload(actual_db, source_file_id, client=None, profile_path=config):
        return {"dataset_id": "dataset", "document_id": "doc", "document_name": "book.md"}

    async def fake_parse(actual_db, source_file_id, client=None):
        return {"code": 0}

    monkeypatch.setattr(rag_sync.api, "convert_source_file", fake_convert)
    monkeypatch.setattr(rag_sync.api, "upload_latest_artifact", fake_upload)
    monkeypatch.setattr(rag_sync.api, "parse_uploaded_document", fake_parse)

    with TestClient(
        create_app(
            profile_path=config,
            db_factory=lambda: db,
            worker_poll_interval=0.01,
        )
    ) as client:
        enqueue_response = client.post(
            "/api/jobs",
            json={
                "kind": "sync_file",
                "source_file_id": source_id,
                "profile_name": "quant-books",
            },
        )
        assert enqueue_response.status_code == 200
        time.sleep(0.05)
        start = time.perf_counter()
        status_response = client.get("/api/status")
        duration = time.perf_counter() - start

    assert status_response.status_code == 200
    assert duration < 0.2


def test_startup_reconciles_stale_profiles_from_files_and_queue(tmp_path: Path):
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
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    current = tmp_path / "current.pdf"
    stale = tmp_path / "stale.pdf"
    current.write_text("current", encoding="utf-8")
    stale.write_text("stale", encoding="utf-8")
    db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(current),
        source_type="book",
        extension="pdf",
        sha256="current",
        size_bytes=current.stat().st_size,
        mtime=current.stat().st_mtime,
        state=SourceState.NEW,
    )
    stale_id = db.upsert_source_file(
        profile_name="quant-books-md",
        source_path=str(stale),
        source_type="book",
        extension="pdf",
        sha256="stale",
        size_bytes=stale.stat().st_size,
        mtime=stale.stat().st_mtime,
        state=SourceState.NEW,
    )
    stale_job_id = db.create_job(
        "sync_file",
        source_file_id=stale_id,
        profile_name="quant-books-md",
    )

    with TestClient(
        create_app(
            profile_path=config,
            db_factory=lambda: db,
            worker_poll_interval=0.01,
        )
    ) as client:
        response = client.get("/api/files")

    assert response.status_code == 200
    files = response.json()["files"]
    assert [row["profile_name"] for row in files] == ["quant-books"]
    jobs = {job["id"]: job for job in db.list_jobs(limit=10)}
    assert jobs[stale_job_id]["status"] == "canceled"
    assert "profile no longer configured" in jobs[stale_job_id]["error_summary"]


def test_startup_requeues_abandoned_running_jobs(tmp_path: Path):
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
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source = tmp_path / "book.pdf"
    source.write_text("book", encoding="utf-8")
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="pdf",
        sha256="book",
        size_bytes=source.stat().st_size,
        mtime=source.stat().st_mtime,
        state=SourceState.NEW,
    )
    running_job_id = db.create_job(
        "sync_file",
        source_file_id=source_id,
        profile_name="quant-books",
    )
    db.update_job_status(running_job_id, "running", progress=0.4)

    with TestClient(
        create_app(
            profile_path=config,
            db_factory=lambda: db,
            worker_poll_interval=60,
            worker_enabled=False,
        )
    ):
        pass

    jobs = {job["id"]: job for job in db.list_jobs(limit=10)}
    assert jobs[running_job_id]["status"] == "queued"
    assert "worker restarted" in jobs[running_job_id]["error_summary"]


def test_bulk_enqueue_jobs_endpoint_creates_jobs_for_selected_files(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_a = tmp_path / "a.md"
    source_b = tmp_path / "b.md"
    source_a.write_text("# A\n", encoding="utf-8")
    source_b.write_text("# B\n", encoding="utf-8")
    first_id = db.upsert_source_file(
        profile_name="quant-articles",
        source_path=str(source_a),
        source_type="article",
        extension="md",
        sha256="a",
        size_bytes=source_a.stat().st_size,
        mtime=source_a.stat().st_mtime,
        state=SourceState.NEW,
    )
    second_id = db.upsert_source_file(
        profile_name="quant-papers",
        source_path=str(source_b),
        source_type="paper",
        extension="md",
        sha256="b",
        size_bytes=source_b.stat().st_size,
        mtime=source_b.stat().st_mtime,
        state=SourceState.NEW,
    )
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.post(
        "/api/jobs/bulk",
        json={"kind": "sync_file", "source_file_ids": [first_id, second_id]},
    )

    assert response.status_code == 200
    assert response.json()["count"] == 2
    with db.connect() as conn:
        jobs = conn.execute(
            "SELECT kind, source_file_id FROM jobs ORDER BY id"
        ).fetchall()
    assert [dict(row) for row in jobs] == [
        {"kind": "sync_file", "source_file_id": first_id},
        {"kind": "sync_file", "source_file_id": second_id},
    ]


def test_bulk_enqueue_jobs_endpoint_can_target_filtered_files(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    book = tmp_path / "book.pdf"
    article = tmp_path / "article.md"
    book.write_text("book", encoding="utf-8")
    article.write_text("# Article\n", encoding="utf-8")
    book_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(book),
        source_type="book",
        extension="pdf",
        sha256="book",
        size_bytes=book.stat().st_size,
        mtime=book.stat().st_mtime,
        state=SourceState.NEW,
    )
    db.upsert_source_file(
        profile_name="quant-articles",
        source_path=str(article),
        source_type="article",
        extension="md",
        sha256="article",
        size_bytes=article.stat().st_size,
        mtime=article.stat().st_mtime,
        state=SourceState.NEW,
    )
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.post(
        "/api/jobs/bulk",
        json={
            "kind": "sync_filtered",
            "filters": {"profile": "quant-books", "sourceType": "book"},
        },
    )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["source_file_ids"] == [book_id]
    with db.connect() as conn:
        jobs = conn.execute(
            "SELECT kind, source_file_id, profile_name FROM jobs ORDER BY id"
        ).fetchall()
    assert [dict(row) for row in jobs] == [
        {"kind": "sync_file", "source_file_id": book_id, "profile_name": "quant-books"}
    ]


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


def test_files_endpoint_includes_latest_artifact_and_ragflow_document(tmp_path: Path):
    source = tmp_path / "book.pdf"
    source.write_text("pdf", encoding="utf-8")
    artifact = tmp_path / "book.md"
    artifact.write_text("# Book", encoding="utf-8")
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="pdf",
        sha256="abc",
        size_bytes=3,
        mtime=1.0,
        state=SourceState.CONVERTED,
    )
    db.add_artifact(
        source_file_id=source_id,
        parser="marker",
        output_path=str(artifact),
        output_sha256="def",
        quality_status="clean",
        warnings_json="[]",
    )
    db.upsert_ragflow_document(
        source_file_id=source_id,
        dataset_id="dataset-id",
        dataset_name="quant-books",
        document_id="doc-id",
        document_name="book.md",
        upload_status="uploaded",
        parse_status="parsed",
        chunk_count=10,
        token_count=100,
    )
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.get("/api/files")

    file_row = response.json()["files"][0]
    assert file_row["artifact"]["parser"] == "marker"
    assert file_row["artifact"]["quality_status"] == "clean"
    assert file_row["ragflow"]["document_id"] == "doc-id"
    assert file_row["ragflow"]["chunk_count"] == 10


def test_file_detail_endpoint_includes_recent_history(tmp_path: Path):
    source = tmp_path / "book.pdf"
    source.write_text("pdf", encoding="utf-8")
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="pdf",
        sha256="abc",
        size_bytes=3,
        mtime=1.0,
        state=SourceState.NEW,
    )
    run_id = db.create_pipeline_run(
        source_file_id=source_id,
        profile_name="quant-books",
        source_type="book",
        parser="marker",
        trigger="sync_file",
    )
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="converted",
        duration_seconds=7.0,
        error_summary="",
    )
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.get(f"/api/files/{source_id}")

    assert response.status_code == 200
    assert response.json()["file"]["id"] == source_id
    assert response.json()["history"][0]["stage"] == "convert"


def test_jobs_endpoint_includes_source_details_queue_position_and_stage(tmp_path: Path):
    source = tmp_path / "book.pdf"
    source.write_text("pdf", encoding="utf-8")
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="pdf",
        sha256="abc",
        size_bytes=3,
        mtime=1.0,
        state=SourceState.NEW,
    )
    db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")
    running_job_id = db.create_job(
        "sync_file",
        source_file_id=source_id,
        profile_name="quant-books",
    )
    db.update_job_status(running_job_id, "running", progress=0.35)
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.get("/api/jobs")

    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert jobs[0]["source_path"] == str(source)
    assert jobs[0]["file_name"] == "book.pdf"
    assert jobs[0]["queue_position"] == 0
    assert jobs[0]["stage"]["key"] == "convert"
    assert jobs[0]["stage"]["label"] == "Marker conversion"
    assert jobs[1]["queue_position"] == 1
    assert jobs[1]["stage"]["status"] == "queued"


def test_jobs_endpoint_orders_running_first_then_queue_order(tmp_path: Path):
    source = tmp_path / "book.pdf"
    source.write_text("pdf", encoding="utf-8")
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="pdf",
        sha256="abc",
        size_bytes=3,
        mtime=1.0,
        state=SourceState.NEW,
    )
    queued_one = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")
    running = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")
    queued_two = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")
    db.update_job_status(running, "running", progress=0.2)
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.get("/api/jobs")

    assert response.status_code == 200
    ids = [job["id"] for job in response.json()["jobs"]]
    assert ids[:3] == [running, queued_one, queued_two]


def test_status_endpoint_includes_active_stage_and_system_metrics(tmp_path: Path, monkeypatch):
    source = tmp_path / "book.pdf"
    source.write_text("pdf", encoding="utf-8")
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="pdf",
        sha256="abc",
        size_bytes=3,
        mtime=1.0,
        state=SourceState.NEW,
    )
    job_id = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")
    db.update_job_status(job_id, "running", progress=0.35)
    db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")

    monkeypatch.setattr(rag_sync.api, "read_system_metrics", lambda: {
        "cpu": {"label": "CPU 62%", "value": 62},
        "memory": {"label": "RAM 41%", "value": 41},
        "gpu": {"label": "GPU 97%", "value": 97},
    })
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["label"] == "1 active · 1 queued"
    assert payload["active"]["stage"]["label"] == "Marker conversion"
    assert payload["active"]["file_name"] == "book.pdf"
    assert payload["system"]["gpu"]["label"] == "GPU 97%"


def test_status_endpoint_finds_running_job_beyond_default_page_limit(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "book.pdf"
    source.write_text("pdf", encoding="utf-8")
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="pdf",
        sha256="abc",
        size_bytes=3,
        mtime=1.0,
        state=SourceState.NEW,
    )
    running_job_id = db.create_job(
        "sync_file",
        source_file_id=source_id,
        profile_name="quant-books",
    )
    db.update_job_status(running_job_id, "running", progress=0.35)
    for _ in range(110):
        db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")

    monkeypatch.setattr(rag_sync.api, "read_system_metrics", lambda: {})
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["active"]["file_name"] == "book.pdf"


def test_queue_pause_and_resume_endpoints_update_status(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    with TestClient(create_app(db_factory=lambda: db)) as client:
        pause = client.post("/api/queue/pause")
        paused_status = client.get("/api/status")
        resume = client.post("/api/queue/resume")
        resumed_status = client.get("/api/status")

        assert pause.status_code == 200
        assert pause.json() == {"paused": True}
        assert paused_status.status_code == 200
        assert paused_status.json()["queue"]["paused"] is True
        assert resume.status_code == 200
        assert resume.json() == {"paused": False}
        assert resumed_status.status_code == 200
        assert resumed_status.json()["queue"]["paused"] is False


def test_queue_kill_endpoint_pauses_queue_and_requests_cancel(tmp_path: Path, monkeypatch):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source = tmp_path / "book.pdf"
    source.write_text("pdf", encoding="utf-8")
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="pdf",
        sha256="abc",
        size_bytes=3,
        mtime=1.0,
        state=SourceState.NEW,
    )
    running_job_id = db.create_job(
        "sync_file",
        source_file_id=source_id,
        profile_name="quant-books",
    )
    db.update_job_status(running_job_id, "running", progress=0.1)
    monkeypatch.setattr(rag_sync.api, "terminate_active_parser_processes", lambda: 1)

    with TestClient(create_app(db_factory=lambda: db)) as client:
        client.app.state.queue.current_job_id = running_job_id
        response = client.post("/api/queue/kill")
        status = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "paused": True,
        "canceled_running_job": True,
        "terminated_processes": 1,
    }
    assert status.json()["queue"]["paused"] is True
