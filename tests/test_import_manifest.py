import json
from pathlib import Path

import pytest

from src.db import RagSyncDb
from src.import_manifest import import_manifest_batch, load_manifest
from src.models import SourceState


def test_manifest_parser_rejects_missing_required_fields(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "batch_id": "b1",
                "profile": "quant-books",
                "parser": "marker",
                "parser_version": "1.10.2",
                "files": [{}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source_relpath"):
        load_manifest(manifest)


def test_manifest_parser_requires_profile_parser_and_parser_version(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "batch_id": "b1",
                "files": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="profile"):
        load_manifest(manifest)


def make_batch_dir(tmp_path: Path, *, source_relpath: str, source_sha256: str) -> Path:
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


def test_import_manifest_batch_imports_only_hash_matches(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = db.upsert_source_file(
        profile_name="quant-books",
        source_path="quant/books/Book.pdf",
        source_type="book",
        extension=".pdf",
        sha256="match-hash",
        size_bytes=123,
        mtime=1.0,
        state=SourceState.NEW,
    )
    batch_dir = make_batch_dir(
        tmp_path,
        source_relpath="quant/books/Book.pdf",
        source_sha256="match-hash",
    )

    summary = import_manifest_batch(db, batch_dir, force=False)

    assert summary["imported"] == 1
    artifact = db.latest_artifact_for_source(source_id)
    assert artifact["parser"] == "marker"


def test_import_manifest_batch_requires_reason_for_force(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    batch_dir = make_batch_dir(
        tmp_path,
        source_relpath="quant/books/Book.pdf",
        source_sha256="remote",
    )

    with pytest.raises(ValueError, match="override reason"):
        import_manifest_batch(db, batch_dir, force=True, reason="")
