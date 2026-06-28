from __future__ import annotations

import json
from pathlib import Path

from rag_sync.parser_experiments import (
    default_variant_specs,
    parser_env_summary,
    score_from_audit_json,
    write_audit_template,
)


def test_default_variant_specs_include_marker_variants() -> None:
    labels = {variant.label for variant in default_variant_specs()}

    assert "marker-auto" in labels
    assert "marker-text" in labels
    assert "marker-ocr-lite" in labels
    assert "marker-ocr-full" in labels


def test_write_audit_template_creates_score_scaffold(project_tmp: Path) -> None:
    source_pdf = project_tmp / "book.pdf"
    source_pdf.write_bytes(b"%PDF")
    markdown_path = project_tmp / "book.md"
    markdown_path.write_text("body", encoding="utf-8")

    path = write_audit_template(
        output_path=project_tmp / "agent-audit.json",
        source_pdf=source_pdf,
        markdown_path=markdown_path,
        target_score=0.9,
        sample_pages=[1, 5, 10],
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["target_score"] == 0.9
    assert payload["overall_score"] is None
    assert payload["verdict"] == "pending"
    assert [row["page_number"] for row in payload["page_scores"]] == [1, 5, 10]


def test_score_from_audit_json_reads_numeric_score(project_tmp: Path) -> None:
    path = project_tmp / "agent-audit.json"
    path.write_text(json.dumps({"overall_score": 0.93}), encoding="utf-8")

    assert score_from_audit_json(path) == 0.93


def test_parser_env_summary_has_expected_keys() -> None:
    summary = parser_env_summary()

    assert "marker_timeout_seconds" in summary
    assert "mineru_timeout_seconds" in summary
    assert "marker_low_memory_ocr" in summary
    assert "mineru_available" in summary
