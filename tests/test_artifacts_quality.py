from pathlib import Path

from rag_sync.artifacts import make_upload_markdown
from rag_sync.quality import check_markdown_quality


def test_make_upload_markdown_preserves_body(project_tmp: Path):
    source = project_tmp / "Article.md"
    source.write_text("# Title\n\nBody", encoding="utf-8")
    out = project_tmp / "out.md"

    make_upload_markdown(
        source_path=source,
        output_path=out,
        source_type="article",
        parser="passthrough",
        sha256="abc",
    )

    text = out.read_text(encoding="utf-8")
    assert "source_type: article" in text
    assert "parser: passthrough" in text
    assert "# Title\n\nBody" in text


def test_quality_blocks_empty_markdown(project_tmp: Path):
    path = project_tmp / "empty.md"
    path.write_text("", encoding="utf-8")

    result = check_markdown_quality(path, math_heavy=True)

    assert result.status == "blocked"
    assert "empty" in result.warnings[0]


def test_quality_warns_on_replacement_chars(project_tmp: Path):
    path = project_tmp / "bad.md"
    path.write_text("This has � replacement chars", encoding="utf-8")

    result = check_markdown_quality(path, math_heavy=False)

    assert result.status == "warning"
    assert any("replacement" in warning for warning in result.warnings)
