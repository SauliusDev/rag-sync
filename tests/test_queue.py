import asyncio
from pathlib import Path

from rag_sync.db import RagSyncDb
from rag_sync.models import JobKind, SourceState
from rag_sync.queue import PersistentJobQueue


def _add_source(db: RagSyncDb, project_tmp: Path) -> int:
    source = project_tmp / "source.md"
    source.write_text("# Example\n", encoding="utf-8")
    return db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(source),
        source_type="book",
        extension="md",
        sha256="abc",
        size_bytes=source.stat().st_size,
        mtime=source.stat().st_mtime,
        state=SourceState.NEW,
    )


def test_enqueue_persists_job(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    queue = PersistentJobQueue(db)
    source_id = _add_source(db, project_tmp)

    job_id = queue.enqueue(
        kind=JobKind.CONVERT,
        source_file_id=source_id,
        profile_name="quant-books",
    )

    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    assert row["kind"] == "convert"
    assert row["status"] == "queued"
    assert row["source_file_id"] == source_id
    assert row["profile_name"] == "quant-books"


def test_run_next_executes_registered_handler_and_records_status(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    queue = PersistentJobQueue(db)
    source_id = _add_source(db, project_tmp)
    calls: list[int] = []

    async def handler(job):
        calls.append(job["id"])
        return {"ok": True}

    queue.register(JobKind.CONVERT, handler)
    job_id = queue.enqueue(
        kind=JobKind.CONVERT,
        source_file_id=source_id,
        profile_name="quant-books",
    )

    asyncio.run(queue.run_next())

    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    assert calls == [job_id]
    assert row["status"] == "completed"
    assert row["progress"] == 1


def test_run_next_marks_handler_error_failed(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    queue = PersistentJobQueue(db)
    source_id = _add_source(db, project_tmp)

    async def handler(job):
        raise RuntimeError("marker failed")

    queue.register(JobKind.CONVERT, handler)
    job_id = queue.enqueue(
        kind=JobKind.CONVERT,
        source_file_id=source_id,
        profile_name="quant-books",
    )

    asyncio.run(queue.run_next())

    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    assert row["status"] == "failed"
    assert row["error_summary"] == "marker failed"
