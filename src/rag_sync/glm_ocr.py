from __future__ import annotations

import base64
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
import httpx

from rag_sync.artifacts import make_upload_markdown_from_text
from rag_sync.ldd import log_event

GLM_OCR_API_URL = "https://api.z.ai/api/paas/v4/layout_parsing"
GLM_OCR_MODEL = "glm-ocr"
GLM_OCR_PRICE_PER_MILLION_TOKENS_USD = 0.03
DEFAULT_ENV_FILE = Path("/home/saulius/atlas-services/rag-sync/.env")
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("RAG_SYNC_GLM_OCR_TIMEOUT_SECONDS", "120"))
DEFAULT_RENDER_DPI = int(os.environ.get("RAG_SYNC_GLM_OCR_RENDER_DPI", "150"))
DEFAULT_MAX_ATTEMPTS = int(os.environ.get("RAG_SYNC_GLM_OCR_MAX_ATTEMPTS", "3"))
DEFAULT_RETRY_DELAY_SECONDS = float(os.environ.get("RAG_SYNC_GLM_OCR_RETRY_DELAY_SECONDS", "5"))
MAX_IMAGE_BYTES = 9_500_000


@dataclass(frozen=True)
class GlmOcrPage:
    page_number: int
    markdown: str
    response: dict[str, Any]


def glm_ocr_raw_dir(output_path: Path) -> Path:
    return output_path.parent / ".parser-raw" / "glm-ocr" / output_path.stem


def read_zai_api_key(env_file: Path = DEFAULT_ENV_FILE) -> str:
    for key in ("Z_AI_API_KEY", "ZHIPUAI_API_KEY", "ZAI_API_KEY"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() in {"Z_AI_API_KEY", "ZHIPUAI_API_KEY", "ZAI_API_KEY"}:
                return value.strip().strip('"').strip("'")
    raise RuntimeError(
        "Z_AI_API_KEY not found in environment or .env; required for glm-ocr"
    )


def extract_markdown(payload: dict[str, Any]) -> str:
    direct = payload.get("md_results")
    if isinstance(direct, str):
        return direct.strip()

    parts: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key in ("md_results", "markdown", "md"):
                nested = value.get(key)
                if isinstance(nested, str) and nested.strip():
                    parts.append(nested.strip())
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return "\n\n".join(dict.fromkeys(parts)).strip()


def _usage_tokens(payload: dict[str, Any]) -> int:
    usage = payload.get("usage")
    if isinstance(usage, dict):
        try:
            return int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _page_file_name(page_number: int, suffix: str) -> str:
    return f"page_{page_number:04d}.{suffix}"


def _merged_page_headers(book_title: str, pages: list[GlmOcrPage]) -> str:
    chunks: list[str] = []
    for page in pages:
        body = page.markdown.strip()
        chunks.append(f"## [p.{page.page_number}] {book_title}\n\n{body}".rstrip())
    return "\n\n".join(chunks).rstrip() + "\n"


def write_glm_ocr_artifacts(
    *,
    source_path: Path,
    output_path: Path,
    source_type: str,
    source_sha256: str,
    book_title: str,
    pages: list[GlmOcrPage],
    duration_seconds: float,
) -> Path:
    raw_dir = glm_ocr_raw_dir(output_path)
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    pages_dir = raw_dir / "pages"
    merged_dir = raw_dir / "merged"
    ragflow_dir = raw_dir / "ragflow"
    pages_dir.mkdir(parents=True, exist_ok=True)
    merged_dir.mkdir(parents=True, exist_ok=True)
    ragflow_dir.mkdir(parents=True, exist_ok=True)

    log_event(
        "glm_ocr.artifacts.write.started",
        "ok",
        source_path=str(source_path),
        output_path=str(output_path),
        page_count=len(pages),
        raw_dir=str(raw_dir),
    )
    page_manifest: dict[str, Any] = {}
    total_tokens = 0
    for page in pages:
        markdown_name = _page_file_name(page.page_number, "md")
        json_name = _page_file_name(page.page_number, "json")
        markdown_path = pages_dir / markdown_name
        json_path = pages_dir / json_name
        markdown_path.write_text(page.markdown.rstrip() + "\n", encoding="utf-8")
        json_path.write_text(
            json.dumps(page.response, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tokens = _usage_tokens(page.response)
        total_tokens += tokens
        page_manifest[str(page.page_number)] = {
            "markdown_path": f"pages/{markdown_name}",
            "json_path": f"pages/{json_name}",
            "tokens": tokens,
            "cost_usd": round(tokens * GLM_OCR_PRICE_PER_MILLION_TOKENS_USD / 1_000_000, 8),
        }

    merged = _merged_page_headers(book_title, pages)
    (merged_dir / "book.page_headers.md").write_text(merged, encoding="utf-8")
    make_upload_markdown_from_text(
        body=merged,
        source_path=source_path,
        output_path=ragflow_dir / "upload.md",
        source_type=source_type,
        parser="glm-ocr",
        sha256=source_sha256,
    )
    make_upload_markdown_from_text(
        body=merged,
        source_path=source_path,
        output_path=output_path,
        source_type=source_type,
        parser="glm-ocr",
        sha256=source_sha256,
    )

    manifest = {
        "book_id": output_path.stem,
        "book_title": book_title,
        "source_pdf": str(source_path),
        "source_sha256": source_sha256,
        "parser": "glm-ocr",
        "page_count": len(pages),
        "duration_seconds": duration_seconds,
        "total_tokens": total_tokens,
        "estimated_cost_usd": round(
            total_tokens * GLM_OCR_PRICE_PER_MILLION_TOKENS_USD / 1_000_000,
            8,
        ),
        "ragflow_upload_path": "ragflow/upload.md",
        "pages": page_manifest,
    }
    (raw_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    log_event(
        "glm_ocr.artifacts.write.finished",
        "ok",
        source_path=str(source_path),
        output_path=str(output_path),
        page_count=len(pages),
        total_tokens=total_tokens,
        estimated_cost_usd=manifest["estimated_cost_usd"],
    )
    return output_path


def _render_page_png(doc: fitz.Document, page_index: int, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    page_number = page_index + 1
    for dpi in (DEFAULT_RENDER_DPI, 120, 96):
        page = doc.load_page(page_index)
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        image_path = output_dir / _page_file_name(page_number, "png")
        pixmap.save(image_path)
        if image_path.stat().st_size <= MAX_IMAGE_BYTES:
            return image_path
    return image_path


def _image_data_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _call_glm_ocr_page(
    *,
    api_key: str,
    image_path: Path,
    source_path: Path,
    page_number: int,
    timeout_seconds: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> dict[str, Any]:
    request_id = f"rag{uuid.uuid4().hex[:29]}"
    payload = {
        "model": GLM_OCR_MODEL,
        "file": _image_data_uri(image_path),
        "return_crop_images": False,
        "need_layout_visualization": False,
        "request_id": request_id,
    }
    started = time.monotonic()
    log_event(
        "glm_ocr.api.page.started",
        "ok",
        source_path=str(source_path),
        page_number=page_number,
        image_path=str(image_path),
        image_bytes=image_path.stat().st_size,
        request_id=request_id,
    )
    attempt = 0
    while True:
        attempt += 1
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.post(
                    GLM_OCR_API_URL,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                data = response.json()
            break
        except Exception as exc:
            retryable = _is_retryable_glm_ocr_error(exc)
            if retryable and attempt < max_attempts:
                log_event(
                    "glm_ocr.api.page.retrying",
                    "error",
                    source_path=str(source_path),
                    page_number=page_number,
                    request_id=request_id,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    retry_delay_seconds=retry_delay_seconds,
                    duration_seconds=time.monotonic() - started,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                if retry_delay_seconds > 0:
                    time.sleep(retry_delay_seconds)
                continue
            log_event(
                "glm_ocr.api.page.failed",
                "error",
                source_path=str(source_path),
                page_number=page_number,
                request_id=request_id,
                attempt=attempt,
                max_attempts=max_attempts,
                retryable=retryable,
                duration_seconds=time.monotonic() - started,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
    if not isinstance(data, dict):
        raise RuntimeError("GLM-OCR response was not a JSON object")
    log_event(
        "glm_ocr.api.page.finished",
        "ok",
        source_path=str(source_path),
        page_number=page_number,
        request_id=request_id,
        duration_seconds=time.monotonic() - started,
        tokens=_usage_tokens(data),
        markdown_bytes=len(extract_markdown(data).encode("utf-8")),
    )
    return data


def _is_retryable_glm_ocr_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.NetworkError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.TimeoutException,
        ),
    )


def convert_pdf_with_glm_ocr(
    *,
    source_path: Path,
    output_path: Path,
    source_type: str,
    source_sha256: str,
    api_key: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    if source_path.suffix.lower() != ".pdf":
        raise RuntimeError(f"glm-ocr only supports PDF inputs in rag-sync: {source_path}")
    resolved_key = api_key or read_zai_api_key()
    started = time.monotonic()
    raw_dir = glm_ocr_raw_dir(output_path)
    render_dir = raw_dir / "rendered-pages"
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    render_dir.mkdir(parents=True, exist_ok=True)
    pages: list[GlmOcrPage] = []

    log_event(
        "glm_ocr.conversion.started",
        "ok",
        source_path=str(source_path),
        output_path=str(output_path),
    )
    try:
        with fitz.open(source_path) as doc:
            page_count = len(doc)
            for page_index in range(page_count):
                page_number = page_index + 1
                log_event(
                    "glm_ocr.render.page.started",
                    "ok",
                    source_path=str(source_path),
                    page_number=page_number,
                    page_count=page_count,
                )
                image_path = _render_page_png(doc, page_index, render_dir)
                log_event(
                    "glm_ocr.render.page.finished",
                    "ok",
                    source_path=str(source_path),
                    page_number=page_number,
                    image_path=str(image_path),
                    image_bytes=image_path.stat().st_size,
                )
                response = _call_glm_ocr_page(
                    api_key=resolved_key,
                    image_path=image_path,
                    source_path=source_path,
                    page_number=page_number,
                    timeout_seconds=timeout_seconds,
                )
                pages.append(
                    GlmOcrPage(
                        page_number=page_number,
                        markdown=extract_markdown(response),
                        response=response,
                    )
                )
    except Exception as exc:
        log_event(
            "glm_ocr.conversion.failed",
            "error",
            source_path=str(source_path),
            output_path=str(output_path),
            duration_seconds=time.monotonic() - started,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise

    result = write_glm_ocr_artifacts(
        source_path=source_path,
        output_path=output_path,
        source_type=source_type,
        source_sha256=source_sha256,
        book_title=source_path.stem,
        pages=pages,
        duration_seconds=time.monotonic() - started,
    )
    log_event(
        "glm_ocr.conversion.finished",
        "ok",
        source_path=str(source_path),
        output_path=str(result),
        page_count=len(pages),
        duration_seconds=time.monotonic() - started,
    )
    return result
