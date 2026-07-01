from pathlib import Path

import pytest
from pypdf import PdfWriter

from tools.parser_benchmark import benchmark_marker


def test_validate_page_range_rejects_start_below_one() -> None:
    with pytest.raises(ValueError, match="page_start must be >= 1"):
        benchmark_marker.validate_page_range(total_pages=50, page_start=0, page_count=10)


def test_validate_page_range_rejects_range_past_end() -> None:
    with pytest.raises(ValueError, match="page range exceeds total pages"):
        benchmark_marker.validate_page_range(total_pages=12, page_start=5, page_count=10)


def test_find_single_markdown_output_returns_only_match(tmp_path: Path) -> None:
    output_dir = tmp_path / "raw"
    output_dir.mkdir()
    only_md = output_dir / "result.md"
    only_md.write_text("# ok\n", encoding="utf-8")

    assert benchmark_marker.find_single_markdown_output(output_dir) == only_md


def test_find_single_markdown_output_rejects_missing_output(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="produced no markdown"):
        benchmark_marker.find_single_markdown_output(tmp_path)


def test_find_single_markdown_output_rejects_multiple_outputs(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("b\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="produced multiple markdown files"):
        benchmark_marker.find_single_markdown_output(tmp_path)


def test_build_summary_contains_duration_and_markdown_size(tmp_path: Path) -> None:
    markdown_path = tmp_path / "sample.md"
    markdown_path.write_text("content", encoding="utf-8")

    summary = benchmark_marker.build_summary(
        source_pdf=Path("/tmp/source.pdf"),
        sample_pdf=Path("/tmp/sample.pdf"),
        run_dir=Path("/tmp/run"),
        page_start=11,
        page_end=20,
        command=["marker", "sample.pdf"],
        duration_seconds=12.5,
        returncode=0,
        markdown_path=markdown_path,
    )

    assert summary["page_start"] == 11
    assert summary["page_end"] == 20
    assert summary["duration_seconds"] == 12.5
    assert summary["markdown_bytes"] == 7


def test_build_marker_command_matches_rag_sync_flags(tmp_path: Path) -> None:
    command = benchmark_marker.build_marker_command(
        marker_bin="/tmp/marker",
        input_dir=tmp_path / ".input",
        output_dir=tmp_path / "out",
    )

    assert command == [
        "/tmp/marker",
        str(tmp_path / ".input"),
        "--output_dir",
        str(tmp_path / "out"),
        "--output_format",
        "markdown",
        "--disable_ocr",
        "--disable_image_extraction",
        "--workers",
        "1",
    ]


def test_write_summary_files_creates_json_and_markdown(tmp_path: Path) -> None:
    summary = {
        "source_pdf": "/tmp/source.pdf",
        "sample_pdf": "/tmp/sample.pdf",
        "run_dir": str(tmp_path),
        "page_start": 1,
        "page_end": 10,
        "command": ["marker", "sample.pdf"],
        "duration_seconds": 3.25,
        "returncode": 0,
        "markdown_path": "/tmp/out/result.md",
        "markdown_bytes": 123,
    }

    benchmark_marker.write_summary_files(tmp_path, summary)

    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "summary.md").exists()
    assert "duration_seconds" in (tmp_path / "summary.json").read_text(encoding="utf-8")
    assert "3.25" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_extract_sample_pdf_copies_requested_page_range(tmp_path: Path) -> None:
    source_pdf = tmp_path / "source.pdf"
    sample_pdf = tmp_path / "sample.pdf"
    writer = PdfWriter()
    for _ in range(5):
        writer.add_blank_page(width=72, height=72)
    with source_pdf.open("wb") as handle:
        writer.write(handle)

    total_pages = benchmark_marker.extract_sample_pdf(
        source_pdf=source_pdf,
        sample_pdf=sample_pdf,
        page_start=2,
        page_end=4,
    )

    assert total_pages == 5
    assert sample_pdf.exists()


def test_stage_marker_input_copies_sample_into_input_directory(tmp_path: Path) -> None:
    sample_pdf = tmp_path / "sample.pdf"
    sample_pdf.write_bytes(b"pdf")

    input_dir = benchmark_marker.stage_marker_input(sample_pdf, tmp_path / "raw")

    assert input_dir == tmp_path / "raw" / ".input"
    assert (input_dir / "sample.pdf").read_bytes() == b"pdf"


def test_default_marker_bin_prefers_local_benchmark_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = Path("/opt/marker/bin/marker")
    monkeypatch.setattr(benchmark_marker, "LOCAL_MARKER_BIN", expected)
    monkeypatch.setattr(benchmark_marker.shutil, "which", lambda name: None)

    assert benchmark_marker._default_marker_bin() == str(expected)
