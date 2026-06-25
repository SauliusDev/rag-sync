from pathlib import Path

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
    )

    rows = db.list_source_files()

    assert len(rows) == 1
    assert rows[0]["id"] == file_id
    assert rows[0]["source_path"] == "/tmp/a.md"
    assert rows[0]["state"] == "new"


def test_db_marks_changed_when_hash_changes(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    db.upsert_source_file("p", "/tmp/a.md", "article", "md", "abc", 12, 1.0, SourceState.NEW)

    db.upsert_source_file("p", "/tmp/a.md", "article", "md", "def", 13, 2.0, SourceState.CHANGED)

    row = db.list_source_files()[0]
    assert row["sha256"] == "def"
    assert row["state"] == "changed"
