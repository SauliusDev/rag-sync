from __future__ import annotations

import json
from pathlib import Path

from rag_sync.visual_audit import (
    append_significant_finding_to_mind,
    markdown_excerpt_for_page,
    prepare_manifest_visual_audit,
    sample_page_numbers,
    strip_frontmatter,
    summarize_book_audit,
    write_batch_summary,
    write_book_audit_report,
    write_prepared_manifest_summary,
    PageAudit,
)


def test_sample_page_numbers_includes_anchor_pages() -> None:
    pages = sample_page_numbers(100, 7, seed=42)

    assert 1 in pages
    assert 50 in pages
    assert 100 in pages
    assert len(pages) == 7
    assert pages == sorted(pages)


def test_strip_frontmatter_removes_yaml_block() -> None:
    text = '---\nsource: "x"\n---\n# Title\n\nBody'

    assert strip_frontmatter(text) == "# Title\n\nBody"


def test_markdown_excerpt_for_page_uses_position() -> None:
    body = "\n".join(f"line {index}" for index in range(1000))
    early = markdown_excerpt_for_page(body, page_number=1, page_count=10, target_chars=200)
    late = markdown_excerpt_for_page(body, page_number=10, page_count=10, target_chars=200)

    assert "line 0" in early
    assert "line 999" in late or "line 998" in late
    assert early != late


def test_summarize_book_audit_rejects_repeated_bad_pages(project_tmp: Path) -> None:
    source = project_tmp / "book.pdf"
    source.write_bytes(b"%PDF")
    markdown = project_tmp / "book.md"
    markdown.write_text("body", encoding="utf-8")
    page_audits = [
        PageAudit(1, 0.2, 0.3, 0.2, "reject", ["missing body"], "bad"),
        PageAudit(5, 0.3, 0.2, 0.4, "reject", ["missing body"], "bad"),
        PageAudit(10, 0.8, 0.7, 0.8, "review", ["ocr noise"], "mixed"),
    ]

    audit = summarize_book_audit(
        source_pdf=source,
        markdown_path=markdown,
        page_count=10,
        sampled_pages=[1, 5, 10],
        page_audits=page_audits,
    )

    assert audit.verdict == "reject"
    assert any("missing body" in reason for reason in audit.reasons)


def test_write_book_audit_report_serializes_payload(project_tmp: Path) -> None:
    source = project_tmp / "book.pdf"
    source.write_bytes(b"%PDF")
    markdown = project_tmp / "book.md"
    markdown.write_text("body", encoding="utf-8")
    audit = summarize_book_audit(
        source_pdf=source,
        markdown_path=markdown,
        page_count=3,
        sampled_pages=[1, 2, 3],
        page_audits=[
            PageAudit(1, 0.95, 0.9, 0.95, "accept", [], "good"),
            PageAudit(2, 0.92, 0.88, 0.91, "accept", [], "good"),
            PageAudit(3, 0.9, 0.85, 0.9, "accept", [], "good"),
        ],
    )

    path = write_book_audit_report(audit, output_path=project_tmp / "audit.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["verdict"] == "accept"
    assert payload["sampled_pages"] == [1, 2, 3]
    assert len(payload["page_audits"]) == 3


def test_write_batch_summary_includes_counts(project_tmp: Path) -> None:
    source = project_tmp / "book.pdf"
    source.write_bytes(b"%PDF")
    markdown = project_tmp / "book.md"
    markdown.write_text("body", encoding="utf-8")
    audits = [
        summarize_book_audit(
            source_pdf=source,
            markdown_path=markdown,
            page_count=1,
            sampled_pages=[1],
            page_audits=[PageAudit(1, 0.95, 0.9, 0.95, "accept", [], "good")],
        ),
        summarize_book_audit(
            source_pdf=source,
            markdown_path=markdown,
            page_count=1,
            sampled_pages=[1],
            page_audits=[PageAudit(1, 0.4, 0.4, 0.4, "reject", ["bad"], "bad")],
        ),
    ]

    path = write_batch_summary(audits, output_path=project_tmp / "summary.md")
    text = path.read_text(encoding="utf-8")

    assert "Accept: `1`" in text
    assert "Reject: `1`" in text


def test_append_significant_finding_to_mind_writes_reject_entry(project_tmp: Path) -> None:
    mind = project_tmp / "mind.md"
    mind.write_text("## history\n\n- existing\n", encoding="utf-8")

    append_significant_finding_to_mind(
        mind_path=mind,
        source_pdf=Path("/tmp/Book.pdf"),
        verdict="reject",
        reasons=["missing body on 3 sampled pages"],
        settings_label="visual audit model=gpt-5.4 sample_pages=7",
    )

    text = mind.read_text(encoding="utf-8")
    assert "Book.pdf" in text
    assert "missing body on 3 sampled pages" in text


def test_prepare_manifest_visual_audit_writes_bundle(
    monkeypatch, project_tmp: Path
) -> None:
    source_pdf = project_tmp / "Book.pdf"
    source_pdf.write_bytes(b"%PDF")
    markdown = project_tmp / "Book.md"
    markdown.write_text("---\na: 1\n---\n# Title\n\nBody", encoding="utf-8")
    manifest = project_tmp / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "status": "ok",
                        "source_filename": "Book.pdf",
                        "source_abspath_cluster": str(source_pdf),
                        "markdown_relpath": "Book.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeReader:
        def __init__(self, _path: str):
            self.pages = [object(), object(), object(), object(), object()]

    monkeypatch.setattr("rag_sync.visual_audit.PdfReader", FakeReader)

    def fake_render(pdf_path: Path, *, page_number: int, output_path: Path, dpi: int = 170) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"png")
        return output_path

    monkeypatch.setattr("rag_sync.visual_audit.render_pdf_page", fake_render)

    prepared = prepare_manifest_visual_audit(
        manifest_path=manifest,
        output_dir=project_tmp / "audit",
        sample_count=3,
        seed=1,
    )

    assert len(prepared) == 1
    bundle_json = project_tmp / "audit" / "Book" / "bundle.json"
    assert bundle_json.exists()
    payload = json.loads(bundle_json.read_text(encoding="utf-8"))
    assert payload["source_pdf"] == str(source_pdf)
    assert len(payload["samples"]) == 3


def test_write_prepared_manifest_summary_lists_books(project_tmp: Path) -> None:
    source = project_tmp / "Book.pdf"
    source.write_bytes(b"%PDF")
    markdown = project_tmp / "Book.md"
    markdown.write_text("body", encoding="utf-8")
    from rag_sync.visual_audit import PreparedBookAudit, PreparedPageSample

    bundle = PreparedBookAudit(
        source_pdf=source,
        markdown_path=markdown,
        page_count=10,
        sampled_pages=[1, 5, 10],
        samples=[
            PreparedPageSample(
                page_number=1,
                image_path=project_tmp / "page-1.png",
                markdown_excerpt_path=project_tmp / "page-1.md",
                markdown_excerpt="body",
            )
        ],
    )

    path = write_prepared_manifest_summary([bundle], output_path=project_tmp / "prep-summary.md")
    text = path.read_text(encoding="utf-8")
    assert "Books prepared: `1`" in text
    assert "Book.pdf" in text
