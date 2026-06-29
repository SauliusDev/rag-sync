import threading
import time
import json
from pathlib import Path

from fastapi.testclient import TestClient

import rag_sync.api
import rag_sync.queue
from rag_sync import ldd
from rag_sync.api import create_app, infer_job_stage
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


def test_settings_endpoint_returns_runtime_and_dataset_defaults(tmp_path: Path, monkeypatch):
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
    monkeypatch.setattr(
        rag_sync.api,
        "openrouter_account_usage",
        lambda: {
            "tracked": False,
            "provider": "openrouter",
            "label": "OpenRouter account",
            "tokens": 0,
            "calls": 0,
            "cost_usd": 0,
            "note": "OPENROUTER_API_KEY is not set.",
        },
    )
    client = TestClient(create_app(profile_path=config, db_factory=lambda: db))

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
    assert data["usage"]["total_cost_usd"] == 0
    assert data["usage"]["providers"]["openrouter"]["tracked"] is False


def test_settings_endpoint_includes_openrouter_account_credits(tmp_path: Path, monkeypatch):
    config = tmp_path / "profiles.toml"
    config.write_text(
        """
[[profiles]]
name = "quant-books"
source_paths = ["/atlas/books"]
file_types = ["pdf"]
parser_mode = "glm-ocr"
target_dataset = "quant-books"
source_type = "book"
""",
        encoding="utf-8",
    )
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()

    monkeypatch.setattr(
        rag_sync.api,
        "openrouter_account_usage",
        lambda: {
            "tracked": True,
            "provider": "openrouter",
            "label": "OpenRouter account",
            "tokens": 0,
            "calls": 0,
            "cost_usd": 46.24717676,
            "total_credits": 60.0,
            "total_usage": 46.24717676,
            "remaining_credits": 13.75282324,
            "note": "Account-level usage from OpenRouter credits API.",
        },
    )

    client = TestClient(create_app(profile_path=config, db_factory=lambda: db))

    response = client.get("/api/settings")

    assert response.status_code == 200
    usage = response.json()["usage"]
    assert usage["total_cost_usd"] == 46.24717676
    assert usage["providers"]["openrouter"]["tracked"] is True
    assert usage["providers"]["openrouter"]["total_credits"] == 60.0
    assert usage["providers"]["openrouter"]["remaining_credits"] == 13.75282324


def test_failed_sync_conversion_error_keeps_conversion_stage_with_stale_artifact():
    stage = infer_job_stage(
        {
            "kind": "sync_file",
            "status": "failed",
            "progress": 0,
            "started_at": "2026-06-29 06:36:16",
            "error_summary": "[Errno -3] Temporary failure in name resolution",
        },
        {
            "artifact": {"parser": "glm-ocr", "created_at": "2026-06-28 20:11:16"},
            "ragflow": None,
        },
    )

    assert stage["key"] == "convert"
    assert stage["label"] == "GLM OCR conversion"
    assert stage["status"] == "failed"


def test_datasets_endpoint_returns_drift_and_coverage(tmp_path: Path, monkeypatch):
    config = tmp_path / "profiles.toml"
    config.write_text(
        """
[[profiles]]
name = "books-marker"
source_paths = ["/atlas/books"]
file_types = ["pdf"]
parser_mode = "marker"
target_dataset = "quant-books"
source_type = "book"

[[profiles]]
name = "papers-glm"
source_paths = ["/atlas/papers"]
file_types = ["pdf"]
parser_mode = "glm-ocr"
target_dataset = "quant-papers"
source_type = "paper"
""",
        encoding="utf-8",
    )
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    parsed_id = db.upsert_source_file(
        profile_name="books-marker",
        source_path="/atlas/books/A.pdf",
        source_type="book",
        extension="pdf",
        sha256="sha-a",
        size_bytes=100,
        mtime=1.0,
        state=SourceState.PARSED,
    )
    failed_id = db.upsert_source_file(
        profile_name="books-marker",
        source_path="/atlas/books/B.pdf",
        source_type="book",
        extension="pdf",
        sha256="sha-b",
        size_bytes=120,
        mtime=2.0,
        state=SourceState.FAILED,
    )
    parsing_id = db.upsert_source_file(
        profile_name="papers-glm",
        source_path="/atlas/papers/C.pdf",
        source_type="paper",
        extension="pdf",
        sha256="sha-c",
        size_bytes=140,
        mtime=3.0,
        state=SourceState.UPLOADED,
    )
    db.upsert_ragflow_document(
        source_file_id=parsed_id,
        dataset_id="books-id",
        dataset_name="quant-books",
        document_id="doc-a",
        document_name="A.md",
        upload_status="uploaded",
        parse_status="parsed",
        chunk_count=11,
        token_count=101,
    )
    db.upsert_ragflow_document(
        source_file_id=failed_id,
        dataset_id="books-id",
        dataset_name="quant-books",
        document_id="doc-b",
        document_name="B.md",
        upload_status="uploaded",
        parse_status="failed",
    )
    db.upsert_ragflow_document(
        source_file_id=parsing_id,
        dataset_id="papers-id",
        dataset_name="quant-papers",
        document_id="doc-c",
        document_name="C.md",
        upload_status="uploaded",
        parse_status="parsing",
    )

    class FakeRagFlowClient:
        async def list_datasets(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "books-id",
                    "name": "quant-books",
                    "chunk_method": "qa",
                    "parser_config": {
                        "chunk_token_num": 1200,
                        "auto_keywords": 1,
                        "auto_questions": 0,
                        "ext": {"toc_extraction": True},
                        "parent_child": {"use_parent_child": False},
                    },
                },
                {
                    "id": "papers-id",
                    "name": "quant-papers",
                    "chunk_method": "naive",
                    "parser_config": {
                        "chunk_token_num": 900,
                        "auto_keywords": 0,
                        "auto_questions": 0,
                        "ext": {"toc_extraction": False},
                        "parent_child": {"use_parent_child": True},
                    },
                },
            ]

    monkeypatch.setattr(rag_sync.api, "RagFlowClient", FakeRagFlowClient)
    client = TestClient(create_app(profile_path=config, db_factory=lambda: db))

    response = client.get("/api/datasets")

    assert response.status_code == 200
    payload = response.json()
    assert payload["remote_error"] is None
    books = next(item for item in payload["datasets"] if item["name"] == "quant-books")
    papers = next(item for item in payload["datasets"] if item["name"] == "quant-papers")

    assert books["exists"] is True
    assert books["coverage"] == {
        "file_count": 2,
        "indexed_documents": 2,
        "parsed_documents": 1,
        "stuck_documents": 0,
        "failed_documents": 1,
        "chunk_count": 11,
    }
    assert books["profiles"] == [
        {
            "name": "books-marker",
            "parser_mode": "marker",
            "source_type": "book",
            "source_paths": ["/atlas/books"],
            "file_count": 2,
        }
    ]
    assert {item["field"] for item in books["drift"]} == {
        "chunk_method",
        "chunk_token_num",
        "auto_keywords",
        "toc_extraction",
        "use_parent_child",
    }

    assert papers["coverage"] == {
        "file_count": 1,
        "indexed_documents": 1,
        "parsed_documents": 0,
        "stuck_documents": 1,
        "failed_documents": 0,
        "chunk_count": 0,
    }
    assert papers["drift"] == []


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


def test_batch_preview_endpoint_returns_file_statuses(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    db.upsert_source_file(
        profile_name="quant-books",
        source_path="quant/books/Book.pdf",
        source_type="book",
        extension=".pdf",
        sha256="abc",
        size_bytes=123,
        mtime=1.0,
        state=SourceState.NEW,
    )
    client = TestClient(create_app(db_factory=lambda: db))

    batch_dir = make_import_batch_dir(
        tmp_path,
        source_relpath="quant/books/Book.pdf",
        source_sha256="abc",
    )
    response = client.post("/api/import-batches/preview", json={"batch_dir": str(batch_dir)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total"] == 1
    assert payload["files"][0]["validation_status"] == "match"


def test_batch_preview_endpoint_treats_explicit_empty_selection_as_zero_files(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    db.upsert_source_file(
        profile_name="quant-books",
        source_path="quant/books/Book.pdf",
        source_type="book",
        extension=".pdf",
        sha256="abc",
        size_bytes=123,
        mtime=1.0,
        state=SourceState.NEW,
    )
    client = TestClient(create_app(db_factory=lambda: db))

    batch_dir = make_import_batch_dir(
        tmp_path,
        source_relpath="quant/books/Book.pdf",
        source_sha256="abc",
    )
    response = client.post(
        "/api/import-batches/preview",
        json={"batch_dir": str(batch_dir), "selected_relpaths": []},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total"] == 0
    assert payload["files"] == []


def test_batch_preview_endpoint_returns_400_for_missing_manifest(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    client = TestClient(create_app(db_factory=lambda: db))

    missing_dir = tmp_path / "missing-batch"
    response = client.post("/api/import-batches/preview", json={"batch_dir": str(missing_dir)})

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid batch_dir or manifest"


def test_batch_import_endpoint_requires_reason_for_force(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    client = TestClient(create_app(db_factory=lambda: db))

    batch_dir = make_import_batch_dir(
        tmp_path,
        source_relpath="quant/books/Book.pdf",
        source_sha256="remote",
    )
    response = client.post(
        "/api/import-batches/import",
        json={"batch_dir": str(batch_dir), "force": True, "reason": "   "},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "force import requires a non-empty reason"


def test_batch_import_endpoint_treats_explicit_empty_selection_as_zero_files(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path="quant/books/Book.pdf",
        source_type="book",
        extension=".pdf",
        sha256="abc",
        size_bytes=123,
        mtime=1.0,
        state=SourceState.NEW,
    )
    client = TestClient(create_app(db_factory=lambda: db))

    batch_dir = make_import_batch_dir(
        tmp_path,
        source_relpath="quant/books/Book.pdf",
        source_sha256="abc",
    )
    response = client.post(
        "/api/import-batches/import",
        json={"batch_dir": str(batch_dir), "selected_relpaths": []},
    )

    assert response.status_code == 200
    assert response.json()["files"] == 0
    assert response.json()["imported"] == 0
    assert db.latest_artifact_for_source(source_id) is None


def test_batch_import_endpoint_returns_400_for_missing_manifest(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    client = TestClient(create_app(db_factory=lambda: db))

    missing_dir = tmp_path / "missing-batch"
    response = client.post("/api/import-batches/import", json={"batch_dir": str(missing_dir)})

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid batch_dir or manifest"


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


def test_status_endpoint_returns_queue_counts(tmp_path: Path, monkeypatch):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    db.record_usage_event(
        provider="z-ai",
        service="glm-ocr",
        model="glm-ocr",
        tokens=1000,
        cost_usd=0.00003,
    )
    db.create_job("convert", source_file_id=None, profile_name="quant-books")
    running_id = db.create_job("parse", source_file_id=None, profile_name="quant-books")
    db.update_job_status(running_id, "running", progress=0.4)
    monkeypatch.setattr(
        rag_sync.api,
        "openrouter_account_usage",
        lambda: {
            "tracked": False,
            "provider": "openrouter",
            "label": "OpenRouter account",
            "tokens": 0,
            "calls": 0,
            "cost_usd": 0,
            "note": "OPENROUTER_API_KEY is not set.",
        },
    )
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
    assert response.json()["usage"]["total_cost_usd"] == 0.00003
    assert response.json()["usage"]["providers"]["z-ai"]["cost_usd"] == 0.00003


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


def test_background_worker_records_completed_stage_history_for_sync_file_job(
    monkeypatch,
    tmp_path: Path,
):
    config = tmp_path / "profiles.toml"
    config.write_text(
        f"""
[[profiles]]
name = "quant-books"
source_paths = ["{tmp_path}"]
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
            rows = db.completed_stage_durations(limit=3)
            if len(rows) == 3:
                break
            time.sleep(0.02)

    assert db.list_jobs(limit=1)[0]["status"] == "completed"
    rows = db.completed_stage_durations(limit=3)
    assert [row["stage"] for row in rows] == ["parse", "upload", "convert"]
    assert all(row["profile_name"] == "quant-books" for row in rows)
    assert all(row["source_type"] == "book" for row in rows)
    assert all(row["extension"] == "pdf" for row in rows)
    assert all(row["parser"] == "marker" for row in rows)
    assert all(row["duration_seconds"] is not None for row in rows)


def test_background_worker_records_current_parser_in_stage_history_for_resync(
    monkeypatch,
    tmp_path: Path,
):
    config = tmp_path / "profiles.toml"
    config.write_text(
        f"""
[[profiles]]
name = "quant-books"
source_paths = ["{tmp_path}"]
file_types = ["pdf"]
parser_mode = "mineru"
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
        state=SourceState.CONVERTED,
    )
    db.add_artifact(
        source_id,
        "marker",
        str(tmp_path / "old-book.md"),
        "old-artifact-sha",
        "ok",
        "[]",
    )

    def fake_convert(actual_db, source_file_id, parser_name=None, profile_path=config):
        return tmp_path / "new-book.md"

    async def fake_upload(actual_db, source_file_id, client=None, profile_path=config):
        return {"dataset_id": "dataset", "document_id": "doc", "document_name": "new-book.md"}

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
            rows = db.completed_stage_durations(limit=3)
            if len(rows) >= 3:
                break
            time.sleep(0.02)

    rows = db.completed_stage_durations(limit=3)
    assert [row["parser"] for row in rows] == ["mineru", "mineru", "mineru"]


def test_background_worker_updates_pipeline_parser_after_marker_fallback(
    monkeypatch,
    tmp_path: Path,
):
    config = tmp_path / "profiles.toml"
    config.write_text(
        f"""
[[profiles]]
name = "quant-books"
source_paths = ["{tmp_path}"]
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
        actual_db.add_artifact(
            source_file_id,
            "mineru",
            str(tmp_path / "book.md"),
            "artifact-sha",
            "clean",
            "[]",
        )
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

    rows = db.completed_stage_durations(limit=3)
    assert [row["parser"] for row in rows] == ["mineru", "mineru", "mineru"]
    with db.session() as conn:
        run = conn.execute(
            "SELECT parser FROM pipeline_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert run is not None
    assert run["parser"] == "mineru"


def test_background_worker_uses_source_profile_parser_when_job_profile_is_missing(
    monkeypatch,
    tmp_path: Path,
):
    config = tmp_path / "profiles.toml"
    config.write_text(
        f"""
[[profiles]]
name = "quant-books"
source_paths = ["{tmp_path}"]
file_types = ["pdf"]
parser_mode = "mineru"
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
        return tmp_path / "book.md"

    monkeypatch.setattr(rag_sync.api, "convert_source_file", fake_convert)

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
                "kind": "convert",
                "source_file_id": source_id,
            },
        )
        assert response.status_code == 200
        for _ in range(50):
            rows = db.completed_stage_durations(limit=1)
            if rows:
                break
            time.sleep(0.02)

    rows = db.completed_stage_durations(limit=1)
    assert rows[0]["parser"] == "mineru"
    with db.session() as conn:
        run = conn.execute(
            "SELECT profile_name, parser FROM pipeline_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert run is not None
    assert run["profile_name"] == "quant-books"
    assert run["parser"] == "mineru"


def test_background_worker_uses_source_profile_when_job_profile_is_stale(
    monkeypatch,
    tmp_path: Path,
):
    config = tmp_path / "profiles.toml"
    config.write_text(
        f"""
[[profiles]]
name = "quant-books"
source_paths = ["{tmp_path}"]
file_types = ["pdf"]
parser_mode = "mineru"
target_dataset = "quant-books"
source_type = "book"

[[profiles]]
name = "stale-profile"
source_paths = ["{tmp_path}"]
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
        return tmp_path / "book.md"

    monkeypatch.setattr(rag_sync.api, "convert_source_file", fake_convert)

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
                "kind": "convert",
                "source_file_id": source_id,
                "profile_name": "stale-profile",
            },
        )
        assert response.status_code == 200
        for _ in range(50):
            rows = db.completed_stage_durations(limit=1)
            if rows:
                break
            time.sleep(0.02)

    rows = db.completed_stage_durations(limit=1)
    assert rows[0]["profile_name"] == "quant-books"
    assert rows[0]["parser"] == "mineru"
    with db.session() as conn:
        run = conn.execute(
            "SELECT profile_name, parser FROM pipeline_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert run is not None
    assert run["profile_name"] == "quant-books"
    assert run["parser"] == "mineru"


def test_background_worker_records_canceled_stage_history_when_job_is_killed(
    monkeypatch,
    tmp_path: Path,
):
    config = tmp_path / "profiles.toml"
    config.write_text(
        f"""
[[profiles]]
name = "quant-books"
source_paths = ["{tmp_path}"]
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
    started = threading.Event()
    queue_holder: dict[str, object] = {}

    def fake_convert(actual_db, source_file_id, parser_name=None, profile_path=config):
        started.set()
        deadline = time.time() + 1
        while time.time() < deadline:
            queue = queue_holder.get("queue")
            if queue is not None and getattr(queue, "cancel_requested_job_ids", set()):
                raise RuntimeError("killed by user")
            time.sleep(0.01)
        raise RuntimeError("expected cancel request")

    monkeypatch.setattr(rag_sync.api, "convert_source_file", fake_convert)
    monkeypatch.setattr(rag_sync.api, "terminate_active_parser_processes", lambda: 1)

    with TestClient(
        create_app(
            profile_path=config,
            db_factory=lambda: db,
            worker_poll_interval=0.01,
        )
    ) as client:
        queue_holder["queue"] = client.app.state.queue
        response = client.post(
            "/api/jobs",
            json={
                "kind": "sync_file",
                "source_file_id": source_id,
                "profile_name": "quant-books",
            },
        )
        assert response.status_code == 200
        assert started.wait(1.0)
        kill_response = client.post("/api/queue/kill")
        assert kill_response.status_code == 200
        for _ in range(50):
            job = db.list_jobs(limit=1)[0]
            if job["status"] == "canceled":
                break
            time.sleep(0.02)

    assert db.list_jobs(limit=1)[0]["status"] == "canceled"
    assert db.recent_stage_events(source_id, limit=1)[0]["status"] == "canceled"
    with db.session() as conn:
        run = conn.execute(
            "SELECT status FROM pipeline_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert run is not None
    assert run["status"] == "canceled"


def test_background_worker_ignores_late_cancel_after_successful_completion(
    monkeypatch,
    tmp_path: Path,
):
    config = tmp_path / "profiles.toml"
    config.write_text(
        f"""
[[profiles]]
name = "quant-books"
source_paths = ["{tmp_path}"]
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
        return tmp_path / "book.md"

    monkeypatch.setattr(rag_sync.api, "convert_source_file", fake_convert)

    with TestClient(
        create_app(
            profile_path=config,
            db_factory=lambda: db,
            worker_poll_interval=0.01,
        )
    ) as client:
        client.app.state.queue.consume_cancel_request = lambda job_id: True
        response = client.post(
            "/api/jobs",
            json={
                "kind": "convert",
                "source_file_id": source_id,
                "profile_name": "quant-books",
            },
        )
        assert response.status_code == 200
        for _ in range(50):
            job = db.list_jobs(limit=1)[0]
            if job["status"] in {"completed", "canceled", "failed"}:
                break
            time.sleep(0.02)

    job = db.list_jobs(limit=1)[0]
    assert job["status"] == "completed"
    assert db.recent_stage_events(source_id, limit=1)[0]["status"] == "completed"
    with db.session() as conn:
        run = conn.execute(
            "SELECT status FROM pipeline_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert run is not None
    assert run["status"] == "completed"


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

    conversion_started = threading.Event()
    release_conversion = threading.Event()

    def fake_convert(actual_db, source_file_id, parser_name=None, profile_path=config):
        conversion_started.set()
        release_conversion.wait(timeout=2)
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
        assert conversion_started.wait(timeout=1)
        status_response = client.get("/api/status")
        release_conversion.set()

    assert status_response.status_code == 200
    assert status_response.json()["queue"]["running"] == 1


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


def test_files_endpoint_refreshes_ragflow_status_from_remote(tmp_path: Path, monkeypatch):
    source = tmp_path / "paper.pdf"
    source.write_text("pdf", encoding="utf-8")
    artifact = tmp_path / "paper.md"
    artifact.write_text("# Paper", encoding="utf-8")
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = db.upsert_source_file(
        profile_name="quant-papers",
        source_path=str(source),
        source_type="paper",
        extension="pdf",
        sha256="abc",
        size_bytes=3,
        mtime=1.0,
        state=SourceState.UPLOADED,
    )
    db.add_artifact(
        source_file_id=source_id,
        parser="glm-ocr",
        output_path=str(artifact),
        output_sha256="def",
        quality_status="clean",
        warnings_json="[]",
    )
    db.upsert_ragflow_document(
        source_file_id=source_id,
        dataset_id="dataset-id",
        dataset_name="quant-papers",
        document_id="doc-id",
        document_name="paper.md",
        upload_status="uploaded",
        parse_status="parsing",
    )

    class FakeRagFlowClient:
        async def list_documents(self, dataset_id: str) -> list[dict[str, object]]:
            assert dataset_id == "dataset-id"
            return [
                {
                    "id": "doc-id",
                    "name": "paper.md",
                    "progress": 1.0,
                    "run": "DONE",
                    "chunk_count": 77,
                    "token_count": 1234,
                }
            ]

    monkeypatch.setattr("rag_sync.sync.RagFlowClient", FakeRagFlowClient)
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.get("/api/files")

    assert response.status_code == 200
    file_row = response.json()["files"][0]
    assert file_row["state"] == "parsed"
    assert file_row["ragflow"]["parse_status"] == "parsed"
    assert file_row["ragflow"]["chunk_count"] == 77
    assert file_row["ragflow"]["token_count"] == 1234


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


def test_jobs_endpoint_includes_eta_wait_confidence_and_timing_basis(
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
    active_job_id = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")
    queued_job_id = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")
    captured_statuses: list[tuple[int, str]] = []
    timing_context = object()
    prepare_calls = 0

    def fake_prepare_timing_context(actual_db):
        nonlocal prepare_calls
        prepare_calls += 1
        assert actual_db is db
        return timing_context

    def fake_estimate_job_timing(actual_db, job, source_row, now=None, timing_context=None):
        captured_statuses.append((int(job["id"]), str(job["status"])))
        assert actual_db is db
        assert timing_context is timing_context_sentinel
        if int(job["id"]) == active_job_id:
            return {
                "eta_seconds": 120,
                "eta_label": "2m remaining",
                "confidence": "live",
                "timing_basis": "live_progress",
            }
        return {
            "eta_seconds": 60,
            "eta_label": "1m remaining",
            "confidence": "low",
            "timing_basis": "convert+book",
        }

    timing_context_sentinel = timing_context
    monkeypatch.setattr(
        rag_sync.api,
        "prepare_timing_context",
        fake_prepare_timing_context,
        raising=False,
    )
    monkeypatch.setattr(rag_sync.api, "estimate_job_timing", fake_estimate_job_timing, raising=False)

    with TestClient(create_app(db_factory=lambda: db, worker_enabled=False)) as client:
        client.app.state.queue.current_job_id = active_job_id
        client.app.state.queue.current_job = {
            "id": active_job_id,
            "kind": "sync_file",
            "status": "queued",
            "profile_name": "quant-books",
            "source_file_id": source_id,
            "progress": 0.25,
            "error_summary": "",
        }

        response = client.get("/api/jobs")

    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert jobs[0]["id"] == active_job_id
    assert jobs[0]["status"] == "running"
    assert jobs[0]["eta_seconds"] == 120
    assert jobs[0]["eta_label"] == "2m remaining"
    assert jobs[0]["wait_seconds"] == 0
    assert jobs[0]["wait_label"] == "0s"
    assert jobs[0]["confidence"] == "live"
    assert jobs[0]["timing_basis"] == "live_progress"
    assert "sample_size" not in jobs[0]
    assert jobs[1]["id"] == queued_job_id
    assert jobs[1]["eta_seconds"] == 60
    assert jobs[1]["eta_label"] == "1m remaining"
    assert jobs[1]["wait_seconds"] == 120
    assert jobs[1]["wait_label"] == "2m"
    assert jobs[1]["confidence"] == "low"
    assert jobs[1]["timing_basis"] == "convert+book"
    assert "sample_size" not in jobs[1]
    assert prepare_calls == 1
    assert captured_statuses == [
        (active_job_id, "running"),
        (queued_job_id, "queued"),
    ]


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


def test_status_endpoint_does_not_count_superseded_failed_jobs(tmp_path: Path):
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
        state=SourceState.PARSED,
    )
    failed_job_id = db.create_job(
        "sync_file",
        source_file_id=source_id,
        profile_name="quant-books",
    )
    db.update_job_status(failed_job_id, "failed", error_summary="marker failed")
    completed_job_id = db.create_job(
        "sync_file",
        source_file_id=source_id,
        profile_name="quant-books",
    )
    db.update_job_status(completed_job_id, "completed", progress=1)
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["queue"]["failed"] == 0
    assert payload["queue"]["completed"] == 1
    assert payload["label"] == "Idle"


def test_status_endpoint_does_not_count_failed_job_for_already_parsed_source(tmp_path: Path):
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
        state=SourceState.PARSED,
    )
    failed_job_id = db.create_job(
        "sync_file",
        source_file_id=source_id,
        profile_name="quant-books",
    )
    db.update_job_status(failed_job_id, "failed", error_summary="old parser failure")
    client = TestClient(create_app(db_factory=lambda: db))

    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["queue"]["failed"] == 0
    assert payload["label"] == "Idle"


def test_status_endpoint_returns_queue_eta_payload(tmp_path: Path, monkeypatch):
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
    active_job_id = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")
    queued_job_id = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")
    captured_statuses: list[tuple[int, str]] = []
    timing_context = object()
    prepare_calls = 0

    def fake_prepare_timing_context(actual_db):
        nonlocal prepare_calls
        prepare_calls += 1
        assert actual_db is db
        return timing_context

    def fake_estimate_job_timing(actual_db, job, source_row, now=None, timing_context=None):
        assert actual_db is db
        assert timing_context is timing_context_sentinel
        return {
            "eta_seconds": 120 if int(job["id"]) == active_job_id else 60,
            "eta_label": "2m remaining" if int(job["id"]) == active_job_id else "1m remaining",
            "confidence": "live" if int(job["id"]) == active_job_id else "low",
            "timing_basis": "live_progress" if int(job["id"]) == active_job_id else "convert+book",
        }

    def fake_estimate_queue_timing(actual_db, jobs, files_by_id, now=None, timing_context=None):
        assert actual_db is db
        assert timing_context is timing_context_sentinel
        captured_statuses.extend((int(job["id"]), str(job["status"])) for job in jobs)
        return {
            "seconds": 300,
            "label": "5m remaining",
            "confidence": "live",
            "throughput_label": "recent median 1m/file",
            "estimated_finish_at": "2026-06-26T10:05:00",
        }

    timing_context_sentinel = timing_context
    monkeypatch.setattr(rag_sync.api, "read_system_metrics", lambda: {})
    monkeypatch.setattr(
        rag_sync.api,
        "prepare_timing_context",
        fake_prepare_timing_context,
        raising=False,
    )
    monkeypatch.setattr(
        rag_sync.api,
        "estimate_job_timing",
        fake_estimate_job_timing,
        raising=False,
    )
    monkeypatch.setattr(
        rag_sync.api,
        "estimate_queue_timing",
        fake_estimate_queue_timing,
        raising=False,
    )

    with TestClient(create_app(db_factory=lambda: db, worker_enabled=False)) as client:
        client.app.state.queue.current_job_id = active_job_id
        client.app.state.queue.current_job = {
            "id": active_job_id,
            "kind": "sync_file",
            "status": "queued",
            "profile_name": "quant-books",
            "source_file_id": source_id,
            "progress": 0.25,
            "error_summary": "",
        }

        response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["queue_eta"] == {
        "seconds": 300,
        "label": "5m remaining",
        "confidence": "live",
        "throughput_label": "recent median 1m/file",
        "estimated_finish_at": "2026-06-26T10:05:00",
    }
    assert "sample_size" not in payload["active"]
    assert prepare_calls == 1
    assert captured_statuses == [
        (active_job_id, "running"),
        (queued_job_id, "queued"),
    ]


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


def test_status_endpoint_falls_back_to_runtime_active_job_when_db_has_none(
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
    job_id = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")

    monkeypatch.setattr(rag_sync.api, "read_system_metrics", lambda: {})
    with TestClient(create_app(db_factory=lambda: db, worker_enabled=False)) as client:
        client.app.state.queue.current_job_id = job_id
        client.app.state.queue.current_job = {
            "id": job_id,
            "kind": "sync_file",
            "status": "queued",
            "profile_name": "quant-books",
            "source_file_id": source_id,
            "progress": 0.0,
            "error_summary": "",
        }

        response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["label"] == "1 active · 0 queued"
    assert payload["queue"]["running"] == 1
    assert payload["queue"]["queued"] == 0
    assert payload["active"]["id"] == job_id
    assert payload["active"]["file_name"] == "book.pdf"
    assert payload["active"]["status"] == "running"


def test_jobs_endpoint_overrides_runtime_active_job_when_db_row_is_not_running(tmp_path: Path):
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
    active_job_id = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")
    queued_job_id = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-books")

    with TestClient(create_app(db_factory=lambda: db, worker_enabled=False)) as client:
        client.app.state.queue.current_job_id = active_job_id
        client.app.state.queue.current_job = {
            "id": active_job_id,
            "kind": "sync_file",
            "status": "queued",
            "profile_name": "quant-books",
            "source_file_id": source_id,
            "progress": 0.0,
            "error_summary": "",
        }

        response = client.get("/api/jobs")

    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert jobs[0]["id"] == active_job_id
    assert jobs[0]["status"] == "running"
    assert jobs[0]["queue_position"] == 0
    assert jobs[1]["id"] == queued_job_id
    assert jobs[1]["status"] == "queued"
    assert jobs[1]["queue_position"] == 1


def test_queue_pause_and_resume_endpoints_update_status(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    log_path = tmp_path / "rag-sync.log"
    ldd.set_log_path_for_tests(log_path)
    try:
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
    finally:
        ldd.set_log_path_for_tests(None)

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert any(record["event"] == "app.lifecycle.started" for record in records)
    assert any(record["event"] == "queue.paused" for record in records)
    assert any(record["event"] == "queue.resumed" for record in records)
    assert any(record["event"] == "app.lifecycle.stopped" for record in records)


def test_worker_loop_recovers_when_run_next_raises(tmp_path: Path, monkeypatch):
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
    job_id = db.create_job(
        "sync_file",
        source_file_id=source_id,
        profile_name="quant-books",
    )
    calls = 0

    async def flaky_run_next(self):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("worker iteration blew up")
        if calls == 2:
            self.db.update_job_status(job_id, "completed", progress=1)
            return True
        return False

    monkeypatch.setattr(rag_sync.queue.PersistentJobQueue, "run_next", flaky_run_next)

    with TestClient(create_app(db_factory=lambda: db, worker_poll_interval=0.01)):
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            with db.connect() as conn:
                row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is not None and row["status"] == "completed":
                break
            time.sleep(0.02)

    with db.connect() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()

    assert row is not None
    assert row["status"] == "completed"
    assert calls >= 2


def test_queue_resume_restarts_worker_when_task_has_stopped(tmp_path: Path, monkeypatch):
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

    with TestClient(create_app(db_factory=lambda: db, worker_poll_interval=0.01)) as client:
        client.app.state.worker_stop_event.set()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if client.app.state.worker_task.done():
                break
            time.sleep(0.02)

        job_id = db.create_job(
            "sync_file",
            source_file_id=source_id,
            profile_name="quant-books",
        )

        async def finish_job_once(self):
            queued = self.db.next_queued_job()
            if queued is None:
                return False
            self.db.update_job_status(int(queued["id"]), "completed", progress=1)
            return True

        monkeypatch.setattr(rag_sync.queue.PersistentJobQueue, "run_next", finish_job_once)

        response = client.post("/api/queue/resume")

        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            with db.connect() as conn:
                row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is not None and row["status"] == "completed":
                break
            time.sleep(0.02)

    with db.connect() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()

    assert response.status_code == 200
    assert response.json() == {"paused": False}
    assert row is not None
    assert row["status"] == "completed"


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
    log_path = tmp_path / "rag-sync.log"
    ldd.set_log_path_for_tests(log_path)
    try:
        with TestClient(create_app(db_factory=lambda: db, worker_enabled=False)) as client:
            client.app.state.queue.current_job_id = running_job_id
            response = client.post("/api/queue/kill")
            status = client.get("/api/status")
    finally:
        ldd.set_log_path_for_tests(None)

    assert response.status_code == 200
    assert response.json() == {
        "paused": True,
        "canceled_running_job": True,
        "terminated_processes": 1,
    }
    assert status.json()["queue"]["paused"] is True
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    kill_events = [record for record in records if record["event"] == "queue.kill_requested"]
    assert kill_events
    assert kill_events[-1]["canceled_running_job"] is True
    assert kill_events[-1]["terminated_processes"] == 1


def test_app_startup_skips_requeue_when_worker_lock_is_already_held(tmp_path: Path):
    db_path = tmp_path / "state.sqlite"
    owner_db = RagSyncDb(db_path)
    owner_db.migrate()
    source = tmp_path / "book.pdf"
    source.write_text("pdf", encoding="utf-8")
    source_id = owner_db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="pdf",
        sha256="abc",
        size_bytes=3,
        mtime=1.0,
        state=SourceState.NEW,
    )
    running_job_id = owner_db.create_job(
        "sync_file",
        source_file_id=source_id,
        profile_name="quant-books",
    )
    owner_db.update_job_status(running_job_id, "running", progress=0.25)
    assert owner_db.acquire_worker_lock("already-running-worker") is True

    app_db = RagSyncDb(db_path)
    with TestClient(create_app(db_factory=lambda: app_db)) as client:
        assert client.app.state.worker_lock_acquired is False
        with app_db.connect() as conn:
            row = conn.execute(
                "SELECT status, started_at, error_summary FROM jobs WHERE id = ?",
                (running_job_id,),
            ).fetchone()

    owner_db.release_worker_lock("already-running-worker")

    assert row is not None
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert row["error_summary"] == ""
