import asyncio
import json
from hashlib import sha256
from pathlib import Path

import pytest

from rag_sync.db import RagSyncDb
from rag_sync.models import ParserMode, Profile, SourceState
from rag_sync.scanner import sha256_file
from rag_sync.sync import (
    DEFAULT_DATA_DIR,
    convert_source_file,
    delete_ragflow_document,
    output_path_for,
    parse_uploaded_document,
    persist_scan,
    restart_ragflow_document,
    upload_latest_artifact,
)


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


def _profile(source_dir: Path, source_type: str = "article") -> Profile:
    return Profile(
        name="quant-articles",
        source_paths=(source_dir,),
        file_types=("md",),
        parser_mode=ParserMode.PASSTHROUGH,
        target_dataset="dataset-name",
        source_type=source_type,
    )


def _add_source(db: RagSyncDb, source_file: Path, source_type: str = "article") -> int:
    return db.upsert_source_file(
        profile_name="quant-articles",
        source_path=str(source_file),
        source_type=source_type,
        extension=source_file.suffix.lstrip("."),
        sha256=sha256_file(source_file),
        size_bytes=source_file.stat().st_size,
        mtime=source_file.stat().st_mtime,
        state=SourceState.NEW,
    )


def _path_hash(source_path: Path) -> str:
    return sha256(str(source_path).encode("utf-8")).hexdigest()[:12]


def test_output_path_for_uses_data_outputs_safe_name_hash_and_does_not_mutate_source(
    project_tmp: Path,
):
    source_file = project_tmp / "Articles" / "A Weird: Name?.md"
    source_file.parent.mkdir()
    source_file.write_text("# Original\n", encoding="utf-8")
    before = source_file.read_text(encoding="utf-8")

    output_path = output_path_for(_profile(source_file.parent), source_file, "passthrough")

    assert output_path == (
        DEFAULT_DATA_DIR
        / f"outputs/quant-articles/passthrough/A_Weird_Name-{_path_hash(source_file)}.md"
    )
    assert source_file.read_text(encoding="utf-8") == before


def test_output_path_for_avoids_same_stem_collisions(project_tmp: Path):
    first = project_tmp / "first" / "report.pdf"
    second = project_tmp / "second" / "report.pdf"
    first.parent.mkdir()
    second.parent.mkdir()

    first_output = output_path_for(_profile(first.parent), first, "marker")
    second_output = output_path_for(_profile(second.parent), second, "marker")

    assert first_output.name.startswith("report-")
    assert second_output.name.startswith("report-")
    assert first_output != second_output


def test_convert_source_file_passthrough_records_artifact_and_converted_state(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp: Path,
):
    source_dir = project_tmp / "articles"
    source_dir.mkdir()
    source_file = source_dir / "example.md"
    source_file.write_text("# Example\n\nBody\n", encoding="utf-8")
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    source_id = _add_source(db, source_file)
    monkeypatch.chdir(project_tmp)
    monkeypatch.setattr("rag_sync.sync.load_profiles", lambda _path: [_profile(source_dir)])

    output_path = convert_source_file(db, source_id)

    artifact = db.latest_artifact_for_source(source_id)
    row = db.list_source_files()[0]
    assert output_path == (
        DEFAULT_DATA_DIR
        / f"outputs/quant-articles/passthrough/example-{_path_hash(source_file)}.md"
    )
    assert artifact is not None
    assert artifact["parser"] == "passthrough"
    assert artifact["quality_status"] == "clean"
    assert json.loads(artifact["warnings_json"]) == []
    assert artifact["output_sha256"] == sha256_file(output_path)
    assert row["state"] == "converted"
    assert source_file.read_text(encoding="utf-8") == "# Example\n\nBody\n"


def test_convert_source_file_records_blocked_artifact_then_raises(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp: Path,
):
    source_dir = project_tmp / "articles"
    source_dir.mkdir()
    source_file = source_dir / "empty.md"
    source_file.write_text("", encoding="utf-8")
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    source_id = _add_source(db, source_file)
    monkeypatch.chdir(project_tmp)
    monkeypatch.setattr("rag_sync.sync.load_profiles", lambda _path: [_profile(source_dir)])

    with pytest.raises(RuntimeError, match="quality check blocked"):
        convert_source_file(db, source_id)

    artifact = db.latest_artifact_for_source(source_id)
    row = db.list_source_files()[0]
    assert artifact is not None
    assert artifact["quality_status"] == "blocked"
    assert json.loads(artifact["warnings_json"]) == ["generated markdown is empty"]
    assert row["state"] == "failed"


class FakeRagFlowClient:
    def __init__(
        self,
        dataset: dict[str, object] | None = None,
        uploaded: dict[str, object] | None = None,
        parse_response: dict[str, object] | None = None,
    ):
        self.dataset = dataset or {"id": "dataset-id", "name": "Dataset Name"}
        self.uploaded = uploaded or {"id": "document-id", "name": "Output.md"}
        self.parse_response = parse_response or {"code": 0}
        self.uploaded_paths: list[Path] = []
        self.parsed: list[tuple[str, list[str]]] = []
        self.stopped: tuple[str, list[str]] | None = None
        self.deleted: tuple[str, list[str]] | None = None

    async def ensure_dataset(self, name: str) -> dict[str, object]:
        self.dataset_name = name
        return self.dataset

    async def upload_document(self, dataset_id: str, path: Path) -> dict[str, object]:
        self.uploaded_dataset_id = dataset_id
        self.uploaded_paths.append(path)
        return self.uploaded

    async def parse_documents(
        self, dataset_id: str, document_ids: list[str]
    ) -> dict[str, object]:
        self.parsed.append((dataset_id, document_ids))
        return self.parse_response

    async def stop_documents(
        self, dataset_id: str, document_ids: list[str]
    ) -> dict[str, object]:
        self.stopped = (dataset_id, document_ids)
        return {"code": 0}

    async def delete_documents(
        self, dataset_id: str, document_ids: list[str]
    ) -> dict[str, object]:
        self.deleted = (dataset_id, document_ids)
        return {"code": 0}


def _converted_source_with_artifact(project_tmp: Path) -> tuple[RagSyncDb, int, Path]:
    source_dir = project_tmp / "articles"
    source_dir.mkdir()
    source_file = source_dir / "example.md"
    source_file.write_text("# Example\n", encoding="utf-8")
    output_path = project_tmp / "output.md"
    output_path.write_text("# Output\n", encoding="utf-8")
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    source_id = _add_source(db, source_file)
    db.add_artifact(
        source_file_id=source_id,
        parser="passthrough",
        output_path=str(output_path),
        output_sha256=sha256_file(output_path),
        quality_status="clean",
        warnings_json="[]",
    )
    return db, source_id, output_path


def test_upload_latest_artifact_records_ragflow_document(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp: Path,
):
    db, source_id, output_path = _converted_source_with_artifact(project_tmp)
    monkeypatch.setattr(
        "rag_sync.sync.load_profiles", lambda _path: [_profile(project_tmp / "articles")]
    )
    client = FakeRagFlowClient()

    result = asyncio.run(upload_latest_artifact(db, source_id, client))

    with db.connect() as conn:
        doc = conn.execute("SELECT * FROM ragflow_documents").fetchone()
    row = db.list_source_files()[0]
    assert result == {
        "dataset_id": "dataset-id",
        "document_id": "document-id",
        "document_name": "Output.md",
    }
    assert client.uploaded_paths == [output_path]
    assert doc["document_id"] == "document-id"
    assert doc["parse_status"] == "not_started"
    assert row["state"] == "uploaded"


def test_upload_latest_artifact_rejects_blocked_artifact(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp: Path,
):
    db, source_id, _ = _converted_source_with_artifact(project_tmp)
    blocked_output = project_tmp / "blocked.md"
    blocked_output.write_text("", encoding="utf-8")
    db.add_artifact(
        source_file_id=source_id,
        parser="passthrough",
        output_path=str(blocked_output),
        output_sha256=sha256_file(blocked_output),
        quality_status="blocked",
        warnings_json='["generated markdown is empty"]',
    )
    monkeypatch.setattr(
        "rag_sync.sync.load_profiles", lambda _path: [_profile(project_tmp / "articles")]
    )
    client = FakeRagFlowClient()

    with pytest.raises(RuntimeError, match="Latest artifact is blocked"):
        asyncio.run(upload_latest_artifact(db, source_id, client))

    assert client.uploaded_paths == []


def test_upload_latest_artifact_errors_when_artifact_missing(project_tmp: Path):
    source_dir = project_tmp / "articles"
    source_dir.mkdir()
    source_file = source_dir / "example.md"
    source_file.write_text("# Example\n", encoding="utf-8")
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    source_id = _add_source(db, source_file)

    with pytest.raises(RuntimeError, match="No artifact found"):
        asyncio.run(upload_latest_artifact(db, source_id, FakeRagFlowClient()))


@pytest.mark.parametrize(
    ("client", "message"),
    [
        (FakeRagFlowClient(dataset={"name": "Missing ID"}), "dataset id"),
        (FakeRagFlowClient(uploaded={"name": "Missing ID"}), "document id"),
    ],
)
def test_upload_latest_artifact_validates_missing_ids(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp: Path,
    client: FakeRagFlowClient,
    message: str,
):
    db, source_id, _ = _converted_source_with_artifact(project_tmp)
    monkeypatch.setattr(
        "rag_sync.sync.load_profiles", lambda _path: [_profile(project_tmp / "articles")]
    )

    with pytest.raises(RuntimeError, match=message):
        asyncio.run(upload_latest_artifact(db, source_id, client))


def test_parse_uploaded_document_marks_parsed(project_tmp: Path):
    db, source_id, _ = _converted_source_with_artifact(project_tmp)
    db.upsert_ragflow_document(
        source_file_id=source_id,
        dataset_id="dataset-id",
        dataset_name="Dataset Name",
        document_id="document-id",
        document_name="Output.md",
        upload_status="uploaded",
        parse_status="not_started",
    )
    client = FakeRagFlowClient(parse_response={"code": 0, "message": "ok"})

    result = asyncio.run(parse_uploaded_document(db, source_id, client))

    with db.connect() as conn:
        doc = conn.execute("SELECT * FROM ragflow_documents").fetchone()
    row = db.list_source_files()[0]
    assert result == {"code": 0, "message": "ok"}
    assert client.parsed == [("dataset-id", ["document-id"])]
    assert doc["parse_status"] == "parsed"
    assert row["state"] == "parsed"


def test_parse_uploaded_document_errors_when_upload_missing(project_tmp: Path):
    source_dir = project_tmp / "articles"
    source_dir.mkdir()
    source_file = source_dir / "example.md"
    source_file.write_text("# Example\n", encoding="utf-8")
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    source_id = _add_source(db, source_file)

    with pytest.raises(RuntimeError, match="No uploaded document found"):
        asyncio.run(parse_uploaded_document(db, source_id, FakeRagFlowClient()))


def test_parse_uploaded_document_rejects_non_uploaded_status(project_tmp: Path):
    db, source_id, _ = _converted_source_with_artifact(project_tmp)
    db.upsert_ragflow_document(
        source_file_id=source_id,
        dataset_id="dataset-id",
        dataset_name="Dataset Name",
        document_id="document-id",
        document_name="Output.md",
        upload_status="failed",
        parse_status="not_started",
    )
    client = FakeRagFlowClient()

    with pytest.raises(RuntimeError, match="No uploaded document found"):
        asyncio.run(parse_uploaded_document(db, source_id, client))

    assert client.parsed == []


def test_delete_ragflow_document_deletes_remote_then_clears_local(project_tmp: Path):
    db, source_id, _ = _converted_source_with_artifact(project_tmp)
    db.upsert_ragflow_document(
        source_file_id=source_id,
        dataset_id="dataset-id",
        dataset_name="Dataset Name",
        document_id="document-id",
        document_name="Output.md",
        upload_status="uploaded",
        parse_status="parsed",
    )
    client = FakeRagFlowClient()

    result = asyncio.run(delete_ragflow_document(db, source_id, client))

    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM ragflow_documents WHERE source_file_id = ?", (source_id,)
        ).fetchone()
    assert result == {"dataset_id": "dataset-id", "document_id": "document-id"}
    assert client.deleted == ("dataset-id", ["document-id"])
    assert row is None


def test_restart_ragflow_document_reuses_latest_artifact(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp: Path,
):
    db, source_id, output_path = _converted_source_with_artifact(project_tmp)
    db.upsert_ragflow_document(
        source_file_id=source_id,
        dataset_id="dataset-id",
        dataset_name="Dataset Name",
        document_id="old-doc",
        document_name="Old.md",
        upload_status="uploaded",
        parse_status="parsed",
    )
    monkeypatch.setattr(
        "rag_sync.sync.load_profiles", lambda _path: [_profile(project_tmp / "articles")]
    )
    client = FakeRagFlowClient(uploaded={"id": "new-doc", "name": "Output.md"})

    result = asyncio.run(restart_ragflow_document(db, source_id, client))

    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM ragflow_documents WHERE source_file_id = ?", (source_id,)
        ).fetchone()
    assert client.deleted == ("dataset-id", ["old-doc"])
    assert client.uploaded_paths == [output_path]
    assert client.parsed == [("dataset-id", ["new-doc"])]
    assert result["document_id"] == "new-doc"
    assert row["document_id"] == "new-doc"
