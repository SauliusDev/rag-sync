import sqlite3
from pathlib import Path

import pytest

from rag_sync.db import RagSyncDb
from rag_sync.models import SourceState


def test_db_upserts_source_file(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()

    file_id = db.upsert_source_file(
        profile_name="quant-articles",
        source_path="/tmp/a.md",
        source_type="article",
        extension="md",
        sha256="abc",
        size_bytes=12,
        mtime=1.0,
        state=SourceState.NEW,
        page_count=42,
        pdf_producer="Example Producer",
    )

    rows = db.list_source_files()

    assert len(rows) == 1
    assert rows[0]["id"] == file_id
    assert rows[0]["source_path"] == "/tmp/a.md"
    assert rows[0]["state"] == "new"
    assert rows[0]["page_count"] == 42
    assert rows[0]["pdf_producer"] == "Example Producer"


def test_db_marks_changed_when_hash_changes(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    db.upsert_source_file("p", "/tmp/a.md", "article", "md", "abc", 12, 1.0, SourceState.NEW)

    db.upsert_source_file("p", "/tmp/a.md", "article", "md", "def", 13, 2.0, SourceState.CHANGED)

    row = db.list_source_files()[0]
    assert row["sha256"] == "def"
    assert row["state"] == "changed"


def test_migrate_is_idempotent(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")

    db.migrate()
    db.migrate()

    assert db.list_source_files() == []


def test_foreign_keys_reject_artifact_with_invalid_source_file_id(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()

    with pytest.raises(sqlite3.IntegrityError), db.connect() as conn:
        conn.execute(
            """
                INSERT INTO artifacts (
                  source_file_id, parser, output_path, output_sha256, quality_status
                ) VALUES (?, ?, ?, ?, ?)
                """,
            (999, "marker", "/tmp/out.md", "abc", "ok"),
        )


def test_same_source_path_can_exist_in_two_profiles(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()

    first_id = db.upsert_source_file(
        "profile-a", "/tmp/shared.md", "article", "md", "abc", 12, 1.0, SourceState.NEW
    )
    second_id = db.upsert_source_file(
        "profile-b", "/tmp/shared.md", "article", "md", "def", 13, 2.0, SourceState.NEW
    )

    rows = db.list_source_files()

    assert first_id != second_id
    assert len(rows) == 2
    assert {row["profile_name"] for row in rows} == {"profile-a", "profile-b"}


def test_upsert_preserves_curation_fields_on_rescan(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    file_id = db.upsert_source_file(
        "profile-a", "/tmp/a.md", "article", "md", "abc", 12, 1.0, SourceState.NEW
    )
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE source_files
            SET included = 0, priority = 10, tags = 'quant,review', note = 'keep'
            WHERE id = ?
            """,
            (file_id,),
        )

    updated_id = db.upsert_source_file(
        "profile-a", "/tmp/a.md", "article", "md", "def", 13, 2.0, SourceState.CHANGED
    )

    row = db.list_source_files()[0]
    assert updated_id == file_id
    assert row["sha256"] == "def"
    assert row["included"] == 0
    assert row["priority"] == 10
    assert row["tags"] == "quant,review"
    assert row["note"] == "keep"


def test_existing_hashes_returns_paths_for_profile_only(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    db.upsert_source_file(
        "profile-a", "/tmp/a.md", "article", "md", "abc", 12, 1.0, SourceState.NEW
    )
    db.upsert_source_file(
        "profile-b", "/tmp/b.md", "article", "md", "def", 13, 2.0, SourceState.NEW
    )

    hashes = db.existing_hashes("profile-a")

    assert hashes == {"/tmp/a.md": "abc"}


def test_mark_missing_absent_paths_commits_missing_state(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    db.upsert_source_file(
        "profile-a", "/tmp/a.md", "article", "md", "abc", 12, 1.0, SourceState.NEW
    )
    db.upsert_source_file(
        "profile-a", "/tmp/b.md", "article", "md", "def", 13, 2.0, SourceState.NEW
    )
    db.upsert_source_file(
        "profile-b", "/tmp/c.md", "article", "md", "ghi", 14, 3.0, SourceState.NEW
    )

    db.mark_missing_absent_paths("profile-a", {"/tmp/a.md"})

    rows = {row["source_path"]: row["state"] for row in db.list_source_files()}
    assert rows["/tmp/a.md"] == "new"
    assert rows["/tmp/b.md"] == "missing"
    assert rows["/tmp/c.md"] == "new"


def test_add_artifact_marks_source_converted_and_latest_returns_newest(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    file_id = db.upsert_source_file(
        "profile-a", "/tmp/a.md", "article", "md", "abc", 12, 1.0, SourceState.NEW
    )

    first_artifact_id = db.add_artifact(
        source_file_id=file_id,
        parser="passthrough",
        output_path="/tmp/out-1.md",
        output_sha256="sha-1",
        quality_status="clean",
        warnings_json="[]",
    )
    second_artifact_id = db.add_artifact(
        source_file_id=file_id,
        parser="passthrough",
        output_path="/tmp/out-2.md",
        output_sha256="sha-2",
        quality_status="warning",
        warnings_json='["warn"]',
    )

    latest = db.latest_artifact_for_source(file_id)
    row = db.list_source_files()[0]

    assert first_artifact_id != second_artifact_id
    assert latest is not None
    assert latest["id"] == second_artifact_id
    assert latest["output_path"] == "/tmp/out-2.md"
    assert latest["warnings_json"] == '["warn"]'
    assert row["state"] == "converted"


def test_latest_artifact_for_source_returns_none_when_missing(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()

    assert db.latest_artifact_for_source(123) is None


def test_upsert_ragflow_document_inserts_updates_single_row_and_marks_uploaded(
    project_tmp: Path,
):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    file_id = db.upsert_source_file(
        "profile-a", "/tmp/a.md", "article", "md", "abc", 12, 1.0, SourceState.CONVERTED
    )

    db.upsert_ragflow_document(
        source_file_id=file_id,
        dataset_id="dataset-1",
        dataset_name="Dataset One",
        document_id="document-1",
        document_name="First.md",
        upload_status="uploaded",
        parse_status="not_started",
    )
    db.upsert_ragflow_document(
        source_file_id=file_id,
        dataset_id="dataset-1",
        dataset_name="Dataset One",
        document_id="document-2",
        document_name="Second.md",
        upload_status="uploaded",
        parse_status="not_started",
        chunk_count=3,
        token_count=30,
    )

    with db.connect() as conn:
        docs = conn.execute("SELECT * FROM ragflow_documents").fetchall()
    row = db.list_source_files()[0]

    assert len(docs) == 1
    assert docs[0]["source_file_id"] == file_id
    assert docs[0]["document_id"] == "document-2"
    assert docs[0]["document_name"] == "Second.md"
    assert docs[0]["chunk_count"] == 3
    assert docs[0]["token_count"] == 30
    assert row["state"] == "uploaded"


def test_migrate_creates_pipeline_history_tables(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()

    with db.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "pipeline_runs" in tables
    assert "pipeline_stage_events" in tables


def test_create_pipeline_run_and_stage_event(project_tmp: Path):
    source_dir = project_tmp / "books"
    source_dir.mkdir()
    source_file = source_dir / "book.pdf"
    source_file.write_text("pdf", encoding="utf-8")
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source_file),
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
    event_id = db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=12.5,
        error_summary="",
        data_json='{"output":"book.md"}',
    )

    with db.connect() as conn:
        run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        event = conn.execute(
            "SELECT * FROM pipeline_stage_events WHERE id = ?", (event_id,)
        ).fetchone()

    assert run["status"] == "running"
    assert run["trigger"] == "sync_file"
    assert event["stage"] == "convert"
    assert event["duration_seconds"] == 12.5


def test_worker_lock_allows_only_one_owner_until_released(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()

    assert db.acquire_worker_lock("worker-a") is True
    assert db.acquire_worker_lock("worker-b") is False

    db.release_worker_lock("worker-a")

    assert db.acquire_worker_lock("worker-b") is True


def test_db_records_batch_import_and_force_override(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_file_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path="/library/quant/books/Book.pdf",
        source_type="book",
        extension="pdf",
        sha256="def",
        size_bytes=123,
        mtime=1.0,
        state=SourceState.NEW,
    )

    batch_id = db.create_import_batch(
        batch_id="batch-1",
        manifest_path="/tmp/batch/manifest.json",
        profile_name="quant-books",
        parser="marker",
        parser_version="1.10.2",
    )
    db.record_import_decision(
        batch_import_id=batch_id,
        source_file_id=source_file_id,
        source_relpath="quant/books/Book.pdf",
        manifest_source_sha256="abc",
        local_source_sha256="def",
        markdown_path="/tmp/batch/outputs/book.md",
        markdown_sha256="ghi",
        validation_status="hash_mismatch",
        import_mode="force",
        override_reason="same edition renamed locally",
        imported=1,
    )

    rows = db.list_import_batch_files(batch_id)
    assert rows[0]["source_file_id"] == source_file_id
    assert rows[0]["validation_status"] == "hash_mismatch"
    assert rows[0]["import_mode"] == "force"
    assert rows[0]["override_reason"] == "same edition renamed locally"


def test_db_record_import_decision_rejects_unknown_source_file_id(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()

    batch_id = db.create_import_batch(
        batch_id="batch-1",
        manifest_path="/tmp/batch/manifest.json",
        profile_name="quant-books",
        parser="marker",
        parser_version="1.10.2",
    )

    with pytest.raises(ValueError, match="source_file_id"):
        db.record_import_decision(
            batch_import_id=batch_id,
            source_file_id=999,
            source_relpath="quant/books/Book.pdf",
            manifest_source_sha256="abc",
            local_source_sha256="def",
            markdown_path="/tmp/batch/outputs/book.md",
            markdown_sha256="ghi",
            validation_status="hash_mismatch",
            import_mode="force",
            override_reason="same edition renamed locally",
            imported=1,
        )
