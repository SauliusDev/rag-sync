from __future__ import annotations

import base64
import json
import os
import random
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any, Literal

import httpx
from pypdf import PdfReader

from rag_sync.ldd import log_event
from rag_sync.scanner import sha256_file

AuditVerdict = Literal["accept", "review", "reject"]


@dataclass(frozen=True)
class PageAudit:
    page_number: int
    text_fidelity: float
    formula_fidelity: float
    coverage: float
    verdict: AuditVerdict
    artifacts: list[str]
    evidence: str


@dataclass(frozen=True)
class BookAudit:
    source_pdf: Path
    markdown_path: Path
    page_count: int
    sampled_pages: list[int]
    page_audits: list[PageAudit]
    average_text_fidelity: float
    average_formula_fidelity: float
    average_coverage: float
    verdict: AuditVerdict
    reasons: list[str]


@dataclass(frozen=True)
class PreparedPageSample:
    page_number: int
    image_path: Path
    markdown_excerpt_path: Path
    markdown_excerpt: str


@dataclass(frozen=True)
class PreparedBookAudit:
    source_pdf: Path
    markdown_path: Path
    page_count: int
    sampled_pages: list[int]
    samples: list[PreparedPageSample]


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    _, separator, body = text[4:].partition("\n---\n")
    if not separator:
        return text
    return body


def sample_page_numbers(page_count: int, sample_count: int, *, seed: int = 0) -> list[int]:
    if page_count <= 0:
        return []
    sample_count = max(1, min(sample_count, page_count))
    anchors = {1, max(1, page_count // 2), page_count}
    rng = random.Random(seed)
    population = [page for page in range(1, page_count + 1) if page not in anchors]
    extra_needed = max(0, sample_count - len(anchors))
    sampled = set(anchors)
    sampled.update(rng.sample(population, k=min(extra_needed, len(population))))
    while len(sampled) < sample_count:
        sampled.add(len(sampled) + 1)
    return sorted(sampled)


def markdown_excerpt_for_page(
    markdown_text: str,
    *,
    page_number: int,
    page_count: int,
    target_chars: int = 3000,
) -> str:
    body = strip_frontmatter(markdown_text).strip()
    if not body:
        return ""
    if page_count <= 1:
        return body[:target_chars]
    fraction = (page_number - 1) / max(1, page_count - 1)
    center = int(len(body) * fraction)
    half_window = max(500, target_chars // 2)
    start = max(0, center - half_window)
    end = min(len(body), center + half_window)
    excerpt = body[start:end]
    if start > 0:
        excerpt = "…\n" + excerpt
    if end < len(body):
        excerpt = excerpt + "\n…"
    return excerpt


def render_pdf_page(
    pdf_path: Path,
    *,
    page_number: int,
    output_path: Path,
    dpi: int = 170,
) -> Path:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is required for visual audit page rendering. Install pymupdf first."
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as doc:
        page = doc.load_page(page_number - 1)
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(output_path)
    return output_path


def prepare_book_audit_bundle(
    *,
    source_pdf: Path,
    markdown_path: Path,
    output_dir: Path,
    sample_count: int,
    seed: int,
    dpi: int = 170,
) -> PreparedBookAudit:
    reader = PdfReader(str(source_pdf))
    page_count = len(reader.pages)
    sampled_pages = sample_page_numbers(page_count, sample_count, seed=seed)
    markdown_text = markdown_path.read_text(encoding="utf-8", errors="replace")
    samples: list[PreparedPageSample] = []
    pages_dir = output_dir / "rendered-pages"
    excerpts_dir = output_dir / "markdown-excerpts"
    pages_dir.mkdir(parents=True, exist_ok=True)
    excerpts_dir.mkdir(parents=True, exist_ok=True)

    for page_number in sampled_pages:
        image_path = pages_dir / f"page-{page_number:04d}.png"
        render_pdf_page(source_pdf, page_number=page_number, output_path=image_path, dpi=dpi)
        excerpt = markdown_excerpt_for_page(
            markdown_text,
            page_number=page_number,
            page_count=page_count,
        )
        excerpt_path = excerpts_dir / f"page-{page_number:04d}.md"
        excerpt_path.write_text(excerpt, encoding="utf-8")
        samples.append(
            PreparedPageSample(
                page_number=page_number,
                image_path=image_path,
                markdown_excerpt_path=excerpt_path,
                markdown_excerpt=excerpt,
            )
        )
    return PreparedBookAudit(
        source_pdf=source_pdf,
        markdown_path=markdown_path,
        page_count=page_count,
        sampled_pages=sampled_pages,
        samples=samples,
    )


def write_prepared_book_bundle(bundle: PreparedBookAudit, *, output_path: Path) -> Path:
    payload = {
        "source_pdf": str(bundle.source_pdf),
        "source_sha256": sha256_file(bundle.source_pdf),
        "markdown_path": str(bundle.markdown_path),
        "markdown_sha256": sha256_file(bundle.markdown_path),
        "page_count": bundle.page_count,
        "sampled_pages": bundle.sampled_pages,
        "samples": [
            {
                "page_number": sample.page_number,
                "image_path": str(sample.image_path),
                "markdown_excerpt_path": str(sample.markdown_excerpt_path),
            }
            for sample in bundle.samples
        ],
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def prepare_manifest_visual_audit(
    *,
    manifest_path: Path,
    output_dir: Path,
    sample_count: int,
    seed: int,
    limit: int | None = None,
    only_files: set[str] | None = None,
    dpi: int = 170,
) -> list[PreparedBookAudit]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("files", [])
    if not isinstance(records, list):
        raise RuntimeError(f"manifest has no file list: {manifest_path}")
    prepared: list[PreparedBookAudit] = []
    manifest_root = manifest_path.parent
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("status") != "ok":
            continue
        source_name = str(record.get("source_filename", ""))
        if only_files and source_name not in only_files:
            continue
        source_pdf = Path(str(record.get("source_abspath_cluster", "")))
        markdown_relpath = str(record.get("markdown_relpath", ""))
        markdown_path = manifest_root / markdown_relpath
        if not source_pdf.exists() or not markdown_path.exists():
            continue
        book_output_dir = output_dir / source_pdf.stem
        bundle = prepare_book_audit_bundle(
            source_pdf=source_pdf,
            markdown_path=markdown_path,
            output_dir=book_output_dir,
            sample_count=sample_count,
            seed=seed,
            dpi=dpi,
        )
        write_prepared_book_bundle(bundle, output_path=book_output_dir / "bundle.json")
        prepared.append(bundle)
        if limit is not None and len(prepared) >= limit:
            break
    return prepared


def write_prepared_manifest_summary(
    prepared: list[PreparedBookAudit], *, output_path: Path
) -> Path:
    lines = [
        "# Visual Audit Prep",
        "",
        f"- Books prepared: `{len(prepared)}`",
        "",
    ]
    for bundle in prepared:
        lines.extend(
            [
                f"## {bundle.source_pdf.stem}",
                "",
                f"- Source PDF: `{bundle.source_pdf}`",
                f"- Markdown: `{bundle.markdown_path}`",
                f"- Page count: `{bundle.page_count}`",
                f"- Sampled pages: `{', '.join(str(page) for page in bundle.sampled_pages)}`",
                f"- Bundle JSON: `{(output_path.parent / bundle.source_pdf.stem / 'bundle.json')}`",
                "",
            ]
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _data_url_for_image(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _coerce_score(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _extract_response_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return str(payload["output_text"])
    output = payload.get("output", [])
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    raise RuntimeError("OpenAI response did not contain readable text output")


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(stripped[start : end + 1])


class OpenAIVisualAuditor:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.4",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 180,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def audit_page(
        self,
        *,
        image_path: Path,
        markdown_excerpt: str,
        book_name: str,
        page_number: int,
        page_count: int,
    ) -> PageAudit:
        prompt = (
            "You are auditing PDF-to-Markdown extraction quality for a RAG pipeline.\n"
            "Compare the page image against the provided markdown excerpt.\n"
            "Judge literal fidelity, not just semantic similarity.\n"
            "Pay special attention to formulas, headings, tables, ordering, OCR corruption, "
            "missing sections, repeated junk, and whether the excerpt appears to correspond to "
            "this page at all.\n"
            "Return strict JSON with keys: page_number, text_fidelity, formula_fidelity, "
            "coverage, verdict, artifacts, evidence.\n"
            "Scores must be 0..1. Verdict must be one of accept, review, reject.\n"
            f"Book: {book_name}\n"
            f"Page number: {page_number} of {page_count}\n"
            "Markdown excerpt follows:\n"
            "```markdown\n"
            f"{markdown_excerpt[:12000]}\n"
            "```"
        )
        body = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": _data_url_for_image(image_path)},
                    ],
                }
            ],
        }
        response = httpx.post(
            f"{self.base_url}/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        parsed = _parse_json_object(_extract_response_text(payload))
        verdict = str(parsed.get("verdict", "review")).strip().lower()
        if verdict not in {"accept", "review", "reject"}:
            verdict = "review"
        artifacts = parsed.get("artifacts", [])
        if not isinstance(artifacts, list):
            artifacts = [str(artifacts)]
        return PageAudit(
            page_number=page_number,
            text_fidelity=_coerce_score(parsed.get("text_fidelity")),
            formula_fidelity=_coerce_score(parsed.get("formula_fidelity")),
            coverage=_coerce_score(parsed.get("coverage")),
            verdict=verdict,  # type: ignore[arg-type]
            artifacts=[str(item) for item in artifacts],
            evidence=str(parsed.get("evidence", "")).strip(),
        )


def summarize_book_audit(
    *,
    source_pdf: Path,
    markdown_path: Path,
    page_count: int,
    sampled_pages: list[int],
    page_audits: list[PageAudit],
) -> BookAudit:
    average_text = mean(result.text_fidelity for result in page_audits)
    average_formula = mean(result.formula_fidelity for result in page_audits)
    average_coverage = mean(result.coverage for result in page_audits)
    reject_count = sum(1 for result in page_audits if result.verdict == "reject")
    review_count = sum(1 for result in page_audits if result.verdict == "review")
    reasons: list[str] = []
    artifact_counts: dict[str, int] = {}
    for result in page_audits:
        for artifact in result.artifacts:
            artifact_counts[artifact] = artifact_counts.get(artifact, 0) + 1
    for artifact, count in sorted(artifact_counts.items(), key=lambda item: (-item[1], item[0])):
        if count >= 2:
            reasons.append(f"{artifact} on {count} sampled pages")
    if average_text < 0.75 or average_coverage < 0.75 or reject_count >= 2:
        verdict: AuditVerdict = "reject"
    elif average_text < 0.9 or average_formula < 0.85 or review_count > 0 or reject_count == 1:
        verdict = "review"
    else:
        verdict = "accept"
    if not reasons:
        if verdict == "accept":
            reasons.append("sampled pages matched the extracted markdown closely")
        elif verdict == "review":
            reasons.append("sampled pages showed mixed fidelity and need human review")
        else:
            reasons.append("sampled pages showed repeated extraction defects")
    return BookAudit(
        source_pdf=source_pdf,
        markdown_path=markdown_path,
        page_count=page_count,
        sampled_pages=sampled_pages,
        page_audits=page_audits,
        average_text_fidelity=round(average_text, 3),
        average_formula_fidelity=round(average_formula, 3),
        average_coverage=round(average_coverage, 3),
        verdict=verdict,
        reasons=reasons,
    )


def write_book_audit_report(audit: BookAudit, *, output_path: Path) -> Path:
    payload = {
        "source_pdf": str(audit.source_pdf),
        "source_sha256": sha256_file(audit.source_pdf),
        "markdown_path": str(audit.markdown_path),
        "markdown_sha256": sha256_file(audit.markdown_path),
        "page_count": audit.page_count,
        "sampled_pages": audit.sampled_pages,
        "average_text_fidelity": audit.average_text_fidelity,
        "average_formula_fidelity": audit.average_formula_fidelity,
        "average_coverage": audit.average_coverage,
        "verdict": audit.verdict,
        "reasons": audit.reasons,
        "page_audits": [asdict(item) for item in audit.page_audits],
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def append_significant_finding_to_mind(
    *,
    mind_path: Path,
    source_pdf: Path,
    verdict: AuditVerdict,
    reasons: list[str],
    settings_label: str,
) -> None:
    if verdict == "accept":
        return
    line = (
        f"| {datetime.now(UTC).date()} | `{source_pdf.name}` visual audit | "
        f"GPT visual audit classified the parse as `{verdict}` after sampled page comparison. "
        f"Reasons: {'; '.join(reasons)} | Last tested settings: {settings_label}. "
        "Update retry strategy or source-file expectations before import. |\n"
    )
    text = mind_path.read_text(encoding="utf-8")
    marker = "## history\n"
    if marker not in text:
        raise RuntimeError(f"mind.md missing expected section marker: {mind_path}")
    updated = text.replace(marker, line + "\n" + marker, 1)
    mind_path.write_text(updated, encoding="utf-8")


def audit_markdown_against_pdf(
    *,
    source_pdf: Path,
    markdown_path: Path,
    output_dir: Path,
    sample_count: int,
    seed: int,
    auditor: OpenAIVisualAuditor,
    dpi: int = 170,
) -> BookAudit:
    reader = PdfReader(str(source_pdf))
    page_count = len(reader.pages)
    sampled_pages = sample_page_numbers(page_count, sample_count, seed=seed)
    markdown_text = markdown_path.read_text(encoding="utf-8", errors="replace")
    page_audits: list[PageAudit] = []

    log_event(
        "visual_audit.started",
        "ok",
        source_pdf=str(source_pdf),
        markdown_path=str(markdown_path),
        page_count=page_count,
        sampled_pages=sampled_pages,
    )
    for page_number in sampled_pages:
        image_path = output_dir / "rendered-pages" / f"page-{page_number:04d}.png"
        render_pdf_page(source_pdf, page_number=page_number, output_path=image_path, dpi=dpi)
        excerpt = markdown_excerpt_for_page(
            markdown_text,
            page_number=page_number,
            page_count=page_count,
        )
        result = auditor.audit_page(
            image_path=image_path,
            markdown_excerpt=excerpt,
            book_name=source_pdf.stem,
            page_number=page_number,
            page_count=page_count,
        )
        log_event(
            "visual_audit.page.finished",
            "ok" if result.verdict == "accept" else "error",
            source_pdf=str(source_pdf),
            page_number=page_number,
            text_fidelity=result.text_fidelity,
            formula_fidelity=result.formula_fidelity,
            coverage=result.coverage,
            verdict=result.verdict,
            artifacts=result.artifacts,
        )
        page_audits.append(result)
    audit = summarize_book_audit(
        source_pdf=source_pdf,
        markdown_path=markdown_path,
        page_count=page_count,
        sampled_pages=sampled_pages,
        page_audits=page_audits,
    )
    log_event(
        "visual_audit.finished",
        "ok" if audit.verdict == "accept" else "error",
        source_pdf=str(source_pdf),
        markdown_path=str(markdown_path),
        verdict=audit.verdict,
        average_text_fidelity=audit.average_text_fidelity,
        average_formula_fidelity=audit.average_formula_fidelity,
        average_coverage=audit.average_coverage,
        reasons=audit.reasons,
    )
    return audit


def audit_manifest(
    *,
    manifest_path: Path,
    output_dir: Path,
    sample_count: int,
    seed: int,
    auditor: OpenAIVisualAuditor,
    limit: int | None = None,
    only_files: set[str] | None = None,
) -> list[BookAudit]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("files", [])
    if not isinstance(records, list):
        raise RuntimeError(f"manifest has no file list: {manifest_path}")
    audits: list[BookAudit] = []
    manifest_root = manifest_path.parent
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("status") != "ok":
            continue
        source_name = str(record.get("source_filename", ""))
        if only_files and source_name not in only_files:
            continue
        source_pdf = Path(str(record.get("source_abspath_cluster", "")))
        markdown_relpath = str(record.get("markdown_relpath", ""))
        markdown_path = manifest_root / markdown_relpath
        if not source_pdf.exists() or not markdown_path.exists():
            continue
        book_output_dir = output_dir / source_pdf.stem
        audits.append(
            audit_markdown_against_pdf(
                source_pdf=source_pdf,
                markdown_path=markdown_path,
                output_dir=book_output_dir,
                sample_count=sample_count,
                seed=seed,
                auditor=auditor,
            )
        )
        if limit is not None and len(audits) >= limit:
            break
    return audits


def write_batch_summary(audits: list[BookAudit], *, output_path: Path) -> Path:
    accept_count = sum(1 for item in audits if item.verdict == "accept")
    review_count = sum(1 for item in audits if item.verdict == "review")
    reject_count = sum(1 for item in audits if item.verdict == "reject")
    lines = [
        "# Visual Audit Summary",
        "",
        f"- Books audited: `{len(audits)}`",
        f"- Accept: `{accept_count}`",
        f"- Review: `{review_count}`",
        f"- Reject: `{reject_count}`",
        "",
    ]
    for audit in audits:
        lines.extend(
            [
                f"## {audit.source_pdf.stem}",
                "",
                f"- Verdict: `{audit.verdict}`",
                f"- Sampled pages: `{', '.join(str(page) for page in audit.sampled_pages)}`",
                f"- Avg text fidelity: `{audit.average_text_fidelity:.3f}`",
                f"- Avg formula fidelity: `{audit.average_formula_fidelity:.3f}`",
                f"- Avg coverage: `{audit.average_coverage:.3f}`",
                f"- Reasons: {'; '.join(audit.reasons)}",
                "",
            ]
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def auditor_from_env(*, model: str, timeout_seconds: int) -> OpenAIVisualAuditor:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for visual audit")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    return OpenAIVisualAuditor(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
