import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rag_sync.cli import app
from rag_sync.db import RagSyncDb


def test_cli_help_exits_successfully() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "RAG Sync" in result.output


def _write_config(config: Path, source_path: Path) -> None:
    config.write_text(
        f"""
[[profiles]]
name = "quant-articles"
parser_mode = "marker"
target_dataset = "dataset-123"
source_type = "articles"
source_paths = ["{source_path}"]
file_types = ["md"]
""",
        encoding="utf-8",
    )


def test_profiles_lists_configured_profiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_path = tmp_path / "articles"
    source_path.mkdir()
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "profiles.toml"
    _write_config(config, Path("articles"))

    result = CliRunner().invoke(app, ["profiles", "--config", str(config)])

    assert result.exit_code == 0
    assert "RAG Sync Profiles" in result.output
    assert "quant-articles" in result.output
    assert "marker" in result.output
    assert "dataset-123" in result.output
    assert "articles" in result.output


def test_scan_persists_files_for_selected_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_path = tmp_path / "articles"
    source_path.mkdir()
    source_file = source_path / "example.md"
    source_file.write_text("# Example\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "profiles.toml"
    _write_config(config, Path("articles"))
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    monkeypatch.setattr("rag_sync.cli.default_db", lambda: db)

    result = CliRunner().invoke(
        app,
        ["scan", "--profile-name", "quant-articles", "--config", str(config)],
    )

    assert result.exit_code == 0
    assert "Scan Results" in result.output
    assert "quant-articles" in result.output
    assert "Stored Files" in result.output
    assert "1" in result.output
    rows = db.list_source_files()
    assert len(rows) == 1
    assert rows[0]["source_path"].endswith("example.md")


def test_scan_errors_for_unknown_profile(tmp_path: Path) -> None:
    source_path = tmp_path / "articles"
    source_path.mkdir()
    config = tmp_path / "profiles.toml"
    _write_config(config, source_path)

    result = CliRunner().invoke(
        app,
        ["scan", "--profile-name", "missing", "--config", str(config)],
    )

    assert result.exit_code == 1
    assert "Unknown profile" in result.output
    assert "missing" in result.output


def test_profiles_errors_for_invalid_config(tmp_path: Path) -> None:
    config = tmp_path / "profiles.toml"
    config.write_text("profiles = []", encoding="utf-8")

    result = CliRunner().invoke(app, ["profiles", "--config", str(config)])

    assert result.exit_code == 1
    assert "Failed to load profiles" in result.output
    assert "profiles must be a non-empty list" in result.output


def test_profiles_errors_for_missing_config(tmp_path: Path) -> None:
    config = tmp_path / "missing.toml"

    result = CliRunner().invoke(app, ["profiles", "--config", str(config)])

    assert result.exit_code == 1
    assert "Failed to load profiles" in result.output
    assert "missing.toml" in result.output


def test_convert_command_prints_output_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db = object()
    config = tmp_path / "profiles.toml"
    output_path = tmp_path / "output.md"
    calls: list[tuple[object, int, str | None, Path]] = []
    monkeypatch.setattr("rag_sync.cli.default_db", lambda: db)
    monkeypatch.setattr(
        "rag_sync.cli.convert_source_file",
        lambda actual_db, source_file_id, parser, profile_path: calls.append(
            (actual_db, source_file_id, parser, profile_path)
        )
        or output_path,
    )

    result = CliRunner().invoke(
        app, ["convert", "42", "--parser", "passthrough", "--config", str(config)]
    )

    assert result.exit_code == 0
    assert str(output_path) in result.output
    assert calls == [(db, 42, "passthrough", config)]


def test_upload_command_prints_document_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db = object()
    config = tmp_path / "profiles.toml"
    calls: list[tuple[object, int, Path]] = []

    async def fake_upload(
        actual_db: object, source_file_id: int, profile_path: Path
    ) -> dict[str, object]:
        calls.append((actual_db, source_file_id, profile_path))
        return {"document_id": "document-123"}

    monkeypatch.setattr("rag_sync.cli.default_db", lambda: db)
    monkeypatch.setattr("rag_sync.cli.upload_latest_artifact", fake_upload)

    result = CliRunner().invoke(app, ["upload", "42", "--config", str(config)])

    assert result.exit_code == 0
    assert "document-123" in result.output
    assert calls == [(db, 42, config)]


def test_parse_command_prints_parsed_message(monkeypatch: pytest.MonkeyPatch):
    db = object()
    calls: list[tuple[object, int]] = []

    async def fake_parse(actual_db: object, source_file_id: int) -> dict[str, object]:
        calls.append((actual_db, source_file_id))
        return {"code": 0}

    monkeypatch.setattr("rag_sync.cli.default_db", lambda: db)
    monkeypatch.setattr("rag_sync.cli.parse_uploaded_document", fake_parse)

    result = CliRunner().invoke(app, ["parse", "42"])

    assert result.exit_code == 0
    assert "Parsed document for source file 42" in result.output
    assert calls == [(db, 42)]


def test_marker_batch_run_command_invokes_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output_dir = tmp_path / "batch"
    calls: list[dict[str, object]] = []

    class Result:
        batch_id = "20260626T120000"
        success_count = 2
        failure_count = 1
        manifest_path = output_dir / "manifest.json"
        log_path = output_dir / "logs" / "run.jsonl"

    monkeypatch.setattr(
        "rag_sync.cli.run_marker_batch",
        lambda **kwargs: calls.append(kwargs) or Result(),
    )

    result = CliRunner().invoke(
        app,
        [
            "marker-batch-run",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--profile",
            "quant-books",
            "--tag",
            "cluster",
            "--tag",
            "nightly",
            "--marker-bin",
            "/tmp/marker",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "profile": "quant-books",
            "tags": ("cluster", "nightly"),
            "marker_bin": "/tmp/marker",
        }
    ]
    payload = json.loads(result.output)
    assert payload["batch_id"] == "20260626T120000"
    assert payload["success_count"] == 2
    assert payload["failure_count"] == 1


def test_marker_batch_run_command_exits_for_invalid_input_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "missing"
    output_dir = tmp_path / "batch"

    monkeypatch.setattr(
        "rag_sync.cli.run_marker_batch",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("input directory does not exist")),
    )

    result = CliRunner().invoke(
        app,
        [
            "marker-batch-run",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--profile",
            "quant-books",
        ],
    )

    assert result.exit_code == 1
    assert "input directory does not exist" in result.output
