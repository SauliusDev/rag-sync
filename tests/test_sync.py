from pathlib import Path

from rag_sync.db import RagSyncDb
from rag_sync.models import ParserMode, Profile
from rag_sync.sync import persist_scan


def test_persist_scan_stores_files_and_marks_removed_files_missing(project_tmp: Path):
    source_dir = project_tmp / "articles"
    source_dir.mkdir()
    source_file = source_dir / "example.md"
    source_file.write_text("# Example\n", encoding="utf-8")
    removed_file = source_dir / "removed.md"
    removed_file.write_text("# Removed\n", encoding="utf-8")
    profile = Profile(
        name="quant-articles",
        source_paths=(source_dir,),
        file_types=("md",),
        parser_mode=ParserMode.PASSTHROUGH,
        target_dataset="dataset-123",
        source_type="article",
    )
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()

    first_ids = persist_scan(db, profile)
    removed_file.unlink()
    second_ids = persist_scan(db, profile)

    rows = {Path(row["source_path"]).name: row for row in db.list_source_files()}
    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert rows["example.md"]["state"] == "unchanged"
    assert rows["removed.md"]["state"] == "missing"
