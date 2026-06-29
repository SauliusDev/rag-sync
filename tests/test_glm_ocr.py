from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from rag_sync import glm_ocr


def test_extract_markdown_prefers_content_markdown() -> None:
    payload = {"data": {"content": {"markdown": "# Page\n\nBody"}}}

    assert glm_ocr.extract_markdown(payload) == "# Page\n\nBody"


def test_extract_markdown_reads_nested_markdown_results() -> None:
    payload = {"data": {"pages": [{"md": "fallback"}, {"markdown": "chosen"}]}}

    assert glm_ocr.extract_markdown(payload) == "fallback\n\nchosen"


def test_write_page_artifacts_generates_manifest_and_page_header_upload(project_tmp: Path):
    output = project_tmp / "outputs" / "book.md"
    source = project_tmp / "Book.pdf"
    source.write_bytes(b"pdf")
    pages = [
        glm_ocr.GlmOcrPage(
            page_number=1,
            markdown="Alpha $x$",
            response={"usage": {"total_tokens": 7}},
        ),
        glm_ocr.GlmOcrPage(
            page_number=2,
            markdown="Beta",
            response={"usage": {"total_tokens": 11}},
        ),
    ]

    artifact = glm_ocr.write_glm_ocr_artifacts(
        source_path=source,
        output_path=output,
        source_type="book",
        source_sha256="abc",
        book_title="Book",
        pages=pages,
        duration_seconds=1.5,
    )

    assert artifact == output
    text = output.read_text(encoding="utf-8")
    assert 'parser: "glm-ocr"' in text
    assert "## [p.1] Book\n\nAlpha $x$" in text
    assert "## [p.2] Book\n\nBeta" in text

    raw_dir = glm_ocr.glm_ocr_raw_dir(output)
    assert (raw_dir / "pages" / "page_0001.md").read_text(encoding="utf-8") == "Alpha $x$\n"
    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parser"] == "glm-ocr"
    assert manifest["page_count"] == 2
    assert manifest["total_tokens"] == 18
    assert manifest["pages"]["1"]["markdown_path"] == "pages/page_0001.md"
    assert (raw_dir / "ragflow" / "upload.md").read_text(encoding="utf-8") == text


def test_zai_api_key_reads_repo_env(project_tmp: Path):
    env_file = project_tmp / ".env"
    env_file.write_text("Z_AI_API_KEY='secret'\n", encoding="utf-8")

    assert glm_ocr.read_zai_api_key(env_file) == "secret"


def test_zai_api_key_fails_loud_when_missing(project_tmp: Path):
    with pytest.raises(RuntimeError, match="Z_AI_API_KEY"):
        glm_ocr.read_zai_api_key(project_tmp / ".env")


def test_glm_ocr_page_retries_transient_connect_error(monkeypatch, project_tmp: Path):
    image = project_tmp / "page.png"
    image.write_bytes(b"image")
    attempts = {"count": 0}

    class FakeClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, *args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise httpx.ConnectError("Temporary failure in name resolution")
            request = httpx.Request("POST", glm_ocr.GLM_OCR_API_URL)
            return httpx.Response(200, request=request, json={"md_results": "Recovered"})

    monkeypatch.setattr(glm_ocr.httpx, "Client", FakeClient)

    payload = glm_ocr._call_glm_ocr_page(
        api_key="key",
        image_path=image,
        source_path=project_tmp / "Book.pdf",
        page_number=1,
        timeout_seconds=1,
        retry_delay_seconds=0,
    )

    assert payload["md_results"] == "Recovered"
    assert attempts["count"] == 2


def test_glm_ocr_page_does_not_retry_bad_request(monkeypatch, project_tmp: Path):
    image = project_tmp / "page.png"
    image.write_bytes(b"image")
    attempts = {"count": 0}

    class FakeClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, *args, **kwargs):
            attempts["count"] += 1
            request = httpx.Request("POST", glm_ocr.GLM_OCR_API_URL)
            return httpx.Response(400, request=request, json={"error": "bad request"})

    monkeypatch.setattr(glm_ocr.httpx, "Client", FakeClient)

    with pytest.raises(httpx.HTTPStatusError):
        glm_ocr._call_glm_ocr_page(
            api_key="key",
            image_path=image,
            source_path=project_tmp / "Book.pdf",
            page_number=1,
            timeout_seconds=1,
            retry_delay_seconds=0,
        )

    assert attempts["count"] == 1
