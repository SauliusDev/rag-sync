import asyncio
import json
from pathlib import Path

from src import ldd
from src.db import RagSyncDb
from src.models import JobKind, SourceState
from src.queue import PersistentJobQueue


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


def test_queue_writes_structured_job_state_logs(project_tmp: Path):
    log_path = project_tmp / "rag-sync.log"
    ldd.set_log_path_for_tests(log_path)
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    queue = PersistentJobQueue(db)
    source_id = _add_source(db, project_tmp)

    async def handler(job):
        return {"ok": True}

    queue.register(JobKind.CONVERT, handler)
    try:
        job_id = queue.enqueue(
            kind=JobKind.CONVERT,
            source_file_id=source_id,
            profile_name="quant-books",
        )
        asyncio.run(queue.run_next())
    finally:
        ldd.set_log_path_for_tests(None)

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    events = [record["event"] for record in records]
    assert events == ["job.queued", "job.running", "job.completed"]
    assert all(record["job_id"] == job_id for record in records)
    assert all(record["source_file_id"] == source_id for record in records)
    assert records[-1]["status"] == "ok"


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


def test_run_next_marks_canceled_when_kill_requested(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    queue = PersistentJobQueue(db)
    source_id = _add_source(db, project_tmp)

    async def handler(job):
        queue.request_cancel_running(job["id"])
        raise RuntimeError("killed subprocess")

    queue.register(JobKind.CONVERT, handler)
    job_id = queue.enqueue(
        kind=JobKind.CONVERT,
        source_file_id=source_id,
        profile_name="quant-books",
    )

    asyncio.run(queue.run_next())

    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    assert row["status"] == "canceled"
    assert row["error_summary"] == "killed by user"


def test_request_cancel_running_tracks_current_job(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    queue = PersistentJobQueue(db)
    queue.current_job_id = 42

    canceled = queue.request_cancel_running()

    assert canceled is True
    assert 42 in queue.cancel_requested_job_ids
