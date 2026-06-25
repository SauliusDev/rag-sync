from pathlib import Path

import pytest
from typer.testing import CliRunner

from rag_sync.cli import app


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


def test_scan_lists_files_for_selected_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_path = tmp_path / "articles"
    source_path.mkdir()
    source_file = source_path / "example.md"
    source_file.write_text("# Example\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "profiles.toml"
    _write_config(config, Path("articles"))

    result = CliRunner().invoke(
        app,
        ["scan", "--profile-name", "quant-articles", "--config", str(config)],
    )

    assert result.exit_code == 0
    assert "Scan Results" in result.output
    assert "quant-articles" in result.output
    assert "new" in result.output
    assert "example.md" in result.output
