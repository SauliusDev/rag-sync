import asyncio
import json
from hashlib import sha256
from pathlib import Path

import pytest

from src import ldd
from src.db import RagSyncDb
from src.models import ParserMode, Profile, SourceState
from src.parsers import ParserResult
from src.scanner import sha256_file
from src.sync import (
    DEFAULT_DATA_DIR,
    convert_source_file,
    delete_ragflow_document,
    output_path_for,
    parse_uploaded_document,
    persist_scan,
    refresh_ragflow_documents,
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


def _add_source(
    db: RagSyncDb,
    source_file: Path,
    source_type: str = "article",
    profile_name: str = "quant-articles",
) -> int:
    return db.upsert_source_file(
        profile_name=profile_name,
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
    monkeypatch.setattr("src.sync.load_profiles", lambda _path: [_profile(source_dir)])

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
    monkeypatch.setattr("src.sync.load_profiles", lambda _path: [_profile(source_dir)])

    with pytest.raises(RuntimeError, match="quality check blocked"):
        convert_source_file(db, source_id)

    artifact = db.latest_artifact_for_source(source_id)
    row = db.list_source_files()[0]
    assert artifact is not None
    assert artifact["quality_status"] == "blocked"
    assert json.loads(artifact["warnings_json"]) == ["generated markdown is empty"]
    assert row["state"] == "failed"


def test_convert_source_file_falls_back_to_mineru_for_pdf_books(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp: Path,
):
    source_dir = project_tmp / "books"
    source_dir.mkdir()
    source_file = source_dir / "example.pdf"
    source_file.write_bytes(b"pdf")
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    source_id = _add_source(db, source_file, source_type="book")
    profile = Profile(
        name="quant-articles",
        source_paths=(source_dir,),
        file_types=("pdf",),
        parser_mode=ParserMode.MARKER,
        target_dataset="dataset-name",
        source_type="book",
    )
    monkeypatch.chdir(project_tmp)
    monkeypatch.setattr("src.sync.load_profiles", lambda _path: [profile])

    class FailingMarker:
        def convert(self, source_path: Path, output_path: Path, source_type: str, sha256: str):
            raise RuntimeError("marker failed")

    class WorkingMineru:
        def convert(self, source_path: Path, output_path: Path, source_type: str, sha256: str):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("# mineru body\n\n$alpha + beta$\n", encoding="utf-8")
            return ParserResult("mineru", output_path, "", "")

    def fake_parser_for_name(parser_name: str):
        if parser_name == "marker":
            return FailingMarker()
        if parser_name == "mineru":
            return WorkingMineru()
        raise AssertionError(f"unexpected parser {parser_name}")

    monkeypatch.setattr("src.sync._parser_for_name", fake_parser_for_name)

    output_path = convert_source_file(db, source_id)

    artifact = db.latest_artifact_for_source(source_id)
    row = db.list_source_files()[0]
    assert output_path == (
        DEFAULT_DATA_DIR
        / f"outputs/quant-articles/mineru/example-{_path_hash(source_file)}.md"
    )
    assert artifact is not None
    assert artifact["parser"] == "mineru"
    assert row["state"] == "converted"


def test_convert_source_file_uses_glm_ocr_profile_default(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp: Path,
):
    source_dir = project_tmp / "books"
    source_dir.mkdir()
    source_file = source_dir / "book.pdf"
    source_file.write_bytes(b"pdf")
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    source_id = _add_source(
        db,
        source_file,
        source_type="book",
        profile_name="quant-books",
    )
    profile = Profile(
        name="quant-books",
        source_paths=(source_dir,),
        file_types=("pdf",),
        parser_mode=ParserMode.GLM_OCR,
        target_dataset="quant-books",
        source_type="book",
    )
    monkeypatch.chdir(project_tmp)
    monkeypatch.setattr("src.sync.load_profiles", lambda _path: [profile])

    class WorkingGlmOcr:
        def convert(self, source_path: Path, output_path: Path, source_type: str, sha256: str):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("# glm body\n\n$x + y$\n", encoding="utf-8")
            return ParserResult("glm-ocr", output_path, "", "")

    monkeypatch.setattr("src.sync._parser_for_name", lambda parser_name: WorkingGlmOcr())

    output_path = convert_source_file(db, source_id)

    artifact = db.latest_artifact_for_source(source_id)
    assert output_path == (
        DEFAULT_DATA_DIR
        / f"outputs/quant-books/glm-ocr/book-{_path_hash(source_file)}.md"
    )
    assert artifact is not None
    assert artifact["parser"] == "glm-ocr"


def test_convert_source_file_falls_back_when_marker_output_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp: Path,
):
    source_dir = project_tmp / "books"
    source_dir.mkdir()
    source_file = source_dir / "empty-marker.pdf"
    source_file.write_bytes(b"pdf")
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    source_id = _add_source(db, source_file, source_type="book")
    profile = Profile(
        name="quant-articles",
        source_paths=(source_dir,),
        file_types=("pdf",),
        parser_mode=ParserMode.MARKER,
        target_dataset="dataset-name",
        source_type="book",
    )
    monkeypatch.chdir(project_tmp)
    monkeypatch.setattr("src.sync.load_profiles", lambda _path: [profile])

    class EmptyMarker:
        def convert(self, source_path: Path, output_path: Path, source_type: str, sha256: str):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("", encoding="utf-8")
            return ParserResult("marker", output_path, "", "")

    class WorkingMineru:
        def convert(self, source_path: Path, output_path: Path, source_type: str, sha256: str):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("# mineru body\n\n$alpha + beta$\n", encoding="utf-8")
            return ParserResult("mineru", output_path, "", "")

    def fake_parser_for_name(parser_name: str):
        if parser_name == "marker":
            return EmptyMarker()
        if parser_name == "mineru":
            return WorkingMineru()
        raise AssertionError(f"unexpected parser {parser_name}")

    monkeypatch.setattr("src.sync._parser_for_name", fake_parser_for_name)

    output_path = convert_source_file(db, source_id)

    with db.connect() as conn:
        artifacts = conn.execute(
            "SELECT parser, quality_status FROM artifacts WHERE source_file_id = ? ORDER BY id",
            (source_id,),
        ).fetchall()
    row = db.list_source_files()[0]
    assert output_path == (
        DEFAULT_DATA_DIR
        / f"outputs/quant-articles/mineru/empty-marker-{_path_hash(source_file)}.md"
    )
    assert [(row["parser"], row["quality_status"]) for row in artifacts] == [
        ("marker", "blocked"),
        ("mineru", "clean"),
    ]
    assert row["state"] == "converted"


def test_convert_source_file_logs_marker_fallback_to_mineru(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp: Path,
):
    source_dir = project_tmp / "books"
    source_dir.mkdir()
    source_file = source_dir / "example.pdf"
    source_file.write_bytes(b"pdf")
    log_path = project_tmp / "rag-sync.log"
    ldd.set_log_path_for_tests(log_path)
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    source_id = _add_source(db, source_file, source_type="book")
    profile = Profile(
        name="quant-articles",
        source_paths=(source_dir,),
        file_types=("pdf",),
        parser_mode=ParserMode.MARKER,
        target_dataset="dataset-name",
        source_type="book",
    )
    monkeypatch.chdir(project_tmp)
    monkeypatch.setattr("src.sync.load_profiles", lambda _path: [profile])

    class FailingMarker:
        def convert(self, source_path: Path, output_path: Path, source_type: str, sha256: str):
            raise RuntimeError("marker failed")

    class WorkingMineru:
        def convert(self, source_path: Path, output_path: Path, source_type: str, sha256: str):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("# mineru body\n", encoding="utf-8")
            return ParserResult("mineru", output_path, "", "")

    def fake_parser_for_name(parser_name: str):
        if parser_name == "marker":
            return FailingMarker()
        if parser_name == "mineru":
            return WorkingMineru()
        raise AssertionError(f"unexpected parser {parser_name}")

    monkeypatch.setattr("src.sync._parser_for_name", fake_parser_for_name)

    try:
        convert_source_file(db, source_id)
    finally:
        ldd.set_log_path_for_tests(None)

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    events = [record["event"] for record in records]
    assert "conversion.started" in events
    assert "conversion.failed" in events
    assert "conversion.fallback.started" in events
    assert "conversion.fallback.completed" in events
    fallback = [
        record for record in records if record["event"] == "conversion.fallback.started"
    ][-1]
    assert fallback["from_parser"] == "marker"
    assert fallback["to_parser"] == "mineru"
    assert fallback["source_file_id"] == source_id


def test_convert_source_file_does_not_fallback_when_parser_explicit(
    monkeypatch: pytest.MonkeyPatch,
    project_tmp: Path,
):
    source_dir = project_tmp / "books"
    source_dir.mkdir()
    source_file = source_dir / "example.pdf"
    source_file.write_bytes(b"pdf")
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    source_id = _add_source(db, source_file, source_type="book")
    profile = Profile(
        name="quant-articles",
        source_paths=(source_dir,),
        file_types=("pdf",),
        parser_mode=ParserMode.MARKER,
        target_dataset="dataset-name",
        source_type="book",
    )
    monkeypatch.chdir(project_tmp)
    monkeypatch.setattr("src.sync.load_profiles", lambda _path: [profile])

    class FailingMarker:
        def convert(self, source_path: Path, output_path: Path, source_type: str, sha256: str):
            raise RuntimeError("marker failed")

    def fake_parser_for_name(parser_name: str):
        if parser_name == "marker":
            return FailingMarker()
        raise AssertionError(f"unexpected parser {parser_name}")

    monkeypatch.setattr("src.sync._parser_for_name", fake_parser_for_name)

    with pytest.raises(RuntimeError, match="marker failed"):
        convert_source_file(db, source_id, parser_name="marker")


class FakeRagFlowClient:
    def __init__(
        self,
        dataset: dict[str, object] | None = None,
        uploaded: dict[str, object] | None = None,
        parse_response: dict[str, object] | None = None,
        documents: list[dict[str, object]] | None = None,
    ):
        self.dataset = dataset or {"id": "dataset-id", "name": "Dataset Name"}
        self.uploaded = uploaded or {"id": "document-id", "name": "Output.md"}
        self.parse_response = parse_response or {"code": 0}
        self.documents = documents or []
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

    async def list_documents(self, dataset_id: str) -> list[dict[str, object]]:
        self.listed_dataset_id = dataset_id
        return list(self.documents)


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
        "src.sync.load_profiles", lambda _path: [_profile(project_tmp / "articles")]
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
        "src.sync.load_profiles", lambda _path: [_profile(project_tmp / "articles")]
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
        "src.sync.load_profiles", lambda _path: [_profile(project_tmp / "articles")]
    )

    with pytest.raises(RuntimeError, match=message):
        asyncio.run(upload_latest_artifact(db, source_id, client))


def test_parse_uploaded_document_marks_parsing_until_ragflow_finishes(project_tmp: Path):
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
    assert doc["parse_status"] == "parsing"
    assert row["state"] == "uploaded"


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
        "src.sync.load_profiles", lambda _path: [_profile(project_tmp / "articles")]
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


def test_refresh_ragflow_documents_updates_local_status_from_remote(project_tmp: Path):
    db, source_id, _ = _converted_source_with_artifact(project_tmp)
    db.upsert_ragflow_document(
        source_file_id=source_id,
        dataset_id="dataset-id",
        dataset_name="Dataset Name",
        document_id="document-id",
        document_name="Output.md",
        upload_status="uploaded",
        parse_status="parsing",
    )
    client = FakeRagFlowClient(
        documents=[
            {
                "id": "document-id",
                "name": "Output.md",
                "progress": 1.0,
                "run": "DONE",
                "chunk_count": 12,
                "token_count": 345,
            }
        ]
    )

    refreshed = asyncio.run(refresh_ragflow_documents(db, client))

    with db.connect() as conn:
        doc = conn.execute("SELECT * FROM ragflow_documents WHERE source_file_id = ?", (source_id,)).fetchone()
        source = conn.execute("SELECT state FROM source_files WHERE id = ?", (source_id,)).fetchone()
    assert refreshed == 1
    assert client.listed_dataset_id == "dataset-id"
    assert doc["parse_status"] == "parsed"
    assert doc["chunk_count"] == 12
    assert doc["token_count"] == 345
    assert source["state"] == "parsed"
