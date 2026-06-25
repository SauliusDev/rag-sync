from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_sync import parsers
from rag_sync.parsers import MarkerParser, MinerUParser, PassthroughParser, build_marker_command


def test_passthrough_parser_writes_upload_copy(project_tmp: Path):
    source = project_tmp / "note.md"
    source.write_text("hello", encoding="utf-8")
    output = project_tmp / "out.md"

    result = PassthroughParser().convert(source, output, "article", "abc")

    assert result.output_path == output
    assert "hello" in output.read_text(encoding="utf-8")


def test_build_marker_command_uses_output_parent(project_tmp: Path):
    source = project_tmp / "marker-input"
    output = project_tmp / "out" / "book.md"
    cmd = build_marker_command(source, output)

    assert cmd[0].endswith("marker")
    assert str(source) in cmd
    assert "--output_dir" in cmd
    assert str(output.parent) in cmd


def test_marker_parser_preserves_original_source_and_ignores_stale_output(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "book.pdf"
    source.write_bytes(b"pdf")
    output = project_tmp / "out" / "book.md"
    output.parent.mkdir()
    output.write_text("stale final output", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: object):
        input_dir = Path(cmd[1])
        assert input_dir.is_dir()
        assert (input_dir / "book.pdf").read_bytes() == b"pdf"
        output_dir = Path(cmd[cmd.index("--output_dir") + 1])
        (output_dir / "book.md").write_text("fresh marker body", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(parsers.subprocess, "run", fake_run)

    result = MarkerParser().convert(source, output, "book", "abc")

    text = output.read_text(encoding="utf-8")
    assert result.output_path == output
    assert 'source_path: "' + str(source) + '"' in text
    assert 'parser: "marker"' in text
    assert "fresh marker body" in text
    assert "stale final output" not in text


def test_mineru_parser_preserves_original_source(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "paper.pdf"
    source.write_bytes(b"pdf")
    output = project_tmp / "out" / "paper.md"

    def fake_run(cmd: list[str], **kwargs: object):
        output_dir = Path(cmd[cmd.index("--output") + 1])
        (output_dir / "nested").mkdir()
        (output_dir / "nested" / "paper.md").write_text("fresh mineru body", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(parsers.subprocess, "run", fake_run)

    result = MinerUParser().convert(source, output, "paper", "abc")

    text = output.read_text(encoding="utf-8")
    assert result.output_path == output
    assert 'source_path: "' + str(source) + '"' in text
    assert 'parser: "mineru"' in text
    assert "fresh mineru body" in text


def test_marker_parser_fails_when_multiple_markdown_outputs(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "book.pdf"
    source.write_bytes(b"pdf")
    output = project_tmp / "out" / "book.md"

    def fake_run(cmd: list[str], **kwargs: object):
        output_dir = Path(cmd[cmd.index("--output_dir") + 1])
        (output_dir / "a.md").write_text("a", encoding="utf-8")
        (output_dir / "b.md").write_text("b", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(parsers.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="multiple markdown files"):
        MarkerParser().convert(source, output, "book", "abc")


def test_mineru_parser_fails_when_no_markdown_output(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "paper.pdf"
    source.write_bytes(b"pdf")
    output = project_tmp / "out" / "paper.md"

    def fake_run(cmd: list[str], **kwargs: object):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(parsers.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="produced no markdown"):
        MinerUParser().convert(source, output, "paper", "abc")


def test_marker_parser_wraps_nonzero_exit(monkeypatch: pytest.MonkeyPatch, project_tmp: Path):
    source = project_tmp / "book.pdf"
    source.write_bytes(b"pdf")
    output = project_tmp / "out" / "book.md"

    def fake_run(cmd: list[str], **kwargs: object):
        return SimpleNamespace(returncode=2, stdout="", stderr="boom")

    monkeypatch.setattr(parsers.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="marker failed"):
        MarkerParser().convert(source, output, "book", "abc")
