from pathlib import Path

from rag_sync.artifacts import make_upload_markdown
from rag_sync.quality import check_markdown_quality


def test_make_upload_markdown_preserves_body(project_tmp: Path):
    source = project_tmp / "Article: #1.md"
    source.write_text("# Title\n\nBody", encoding="utf-8")
    out = project_tmp / "nested" / "out.md"

    result = make_upload_markdown(
        source_path=source,
        output_path=out,
        source_type="article",
        parser="passthrough",
        sha256="abc",
    )

    text = out.read_text(encoding="utf-8")
    assert result == out
    assert f'source_path: "{source}"' in text
    assert 'source_type: "article"' in text
    assert 'parser: "passthrough"' in text
    assert 'sha256: "abc"' in text
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


def test_quality_blocks_frontmatter_only_markdown(project_tmp: Path):
    source = project_tmp / "empty.md"
    source.write_text("", encoding="utf-8")
    out = project_tmp / "out.md"
    make_upload_markdown(
        source_path=source,
        output_path=out,
        source_type="article",
        parser="passthrough",
        sha256="abc",
    )

    result = check_markdown_quality(out, math_heavy=True)

    assert result.status == "blocked"
    assert "empty" in result.warnings[0]


def test_quality_warns_on_formula_placeholder(project_tmp: Path):
    path = project_tmp / "formula.md"
    path.write_text("Body\n<!-- formula-not-decoded -->", encoding="utf-8")

    result = check_markdown_quality(path, math_heavy=False)

    assert result.status == "warning"
    assert any("formula placeholder" in warning for warning in result.warnings)


def test_quality_warns_when_math_heavy_without_equations(project_tmp: Path):
    path = project_tmp / "math.md"
    path.write_text("Body without math delimiters", encoding="utf-8")

    result = check_markdown_quality(path, math_heavy=True)

    assert result.status == "warning"
    assert any("no obvious equations" in warning for warning in result.warnings)


def test_quality_math_heuristic_ignores_frontmatter(project_tmp: Path):
    source = project_tmp / "price-$-source.md"
    source.write_text("Body without math delimiters", encoding="utf-8")
    out = project_tmp / "out.md"
    make_upload_markdown(
        source_path=source,
        output_path=out,
        source_type="paper",
        parser="marker",
        sha256="abc",
    )

    result = check_markdown_quality(out, math_heavy=True)

    assert result.status == "warning"
    assert any("no obvious equations" in warning for warning in result.warnings)


def test_quality_clean_for_valid_markdown(project_tmp: Path):
    path = project_tmp / "clean.md"
    path.write_text("Body with $x + y$ equation", encoding="utf-8")

    result = check_markdown_quality(path, math_heavy=True)

    assert result.status == "clean"
    assert result.warnings == []
