from pathlib import Path

from rag_sync.parsers import PassthroughParser, build_marker_command


def test_passthrough_parser_writes_upload_copy(project_tmp: Path):
    source = project_tmp / "note.md"
    source.write_text("hello", encoding="utf-8")
    output = project_tmp / "out.md"

    result = PassthroughParser().convert(source, output, "article", "abc")

    assert result.output_path == output
    assert "hello" in output.read_text(encoding="utf-8")


def test_build_marker_command_uses_output_parent(project_tmp: Path):
    source = project_tmp / "book.pdf"
    output = project_tmp / "out" / "book.md"
    cmd = build_marker_command(source, output)

    assert cmd[0].endswith("marker")
    assert str(source) in cmd
    assert "--output_dir" in cmd
    assert str(output.parent) in cmd
