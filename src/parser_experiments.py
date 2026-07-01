from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from src.ldd import log_event
from src.parsers import (
    convert_with_marker,
    convert_with_mineru,
    mineru_available,
)
from src.quality import check_markdown_quality
from src.scanner import pdf_metadata, sha256_file
from src.visual_audit import prepare_book_audit_bundle, write_prepared_book_bundle

CandidateState = Literal["pending", "ok", "failed", "skipped"]


@dataclass(frozen=True)
class VariantSpec:
    label: str
    parser: Literal["marker", "mineru"]
    description: str
    marker_disable_ocr: bool | None = None
    marker_workers: int = 1
    marker_low_memory_ocr: bool | None = None
    marker_extra_args: tuple[str, ...] = ()
    mineru_method: str = "txt"
    mineru_backend: str = "pipeline"
    mineru_extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateResult:
    label: str
    parser: str
    state: CandidateState
    description: str
    output_path: Path
    audit_bundle_path: Path | None
    duration_seconds: float
    markdown_size_bytes: int
    quality_status: str
    quality_warnings: list[str]
    error: str | None


def default_variant_specs() -> list[VariantSpec]:
    variants = [
        VariantSpec(
            label="marker-auto",
            parser="marker",
            description="Marker with default OCR auto-decision",
        ),
        VariantSpec(
            label="marker-text",
            parser="marker",
            description="Marker with OCR disabled",
            marker_disable_ocr=True,
        ),
        VariantSpec(
            label="marker-ocr-lite",
            parser="marker",
            description="Marker OCR with low-memory settings and repeated-text dropping",
            marker_disable_ocr=False,
            marker_low_memory_ocr=True,
        ),
        VariantSpec(
            label="marker-ocr-full",
            parser="marker",
            description="Marker OCR without low-memory throttling plus repeated-text dropping",
            marker_disable_ocr=False,
            marker_low_memory_ocr=False,
            marker_extra_args=("--drop_repeated_text",),
        ),
    ]
    if mineru_available():
        variants.extend(
            [
                VariantSpec(
                    label="mineru-txt",
                    parser="mineru",
                    description="MinerU pipeline txt extraction with formulas and tables",
                ),
            ]
        )
    return variants


def write_audit_template(
    *,
    output_path: Path,
    source_pdf: Path,
    markdown_path: Path,
    target_score: float,
    sample_pages: list[int],
) -> Path:
    payload = {
        "source_pdf": str(source_pdf),
        "source_sha256": sha256_file(source_pdf),
        "markdown_path": str(markdown_path),
        "markdown_sha256": sha256_file(markdown_path) if markdown_path.exists() else None,
        "target_score": target_score,
        "overall_score": None,
        "verdict": "pending",
        "reasons": [],
        "sampled_pages": sample_pages,
        "page_scores": [
            {
                "page_number": page,
                "text_fidelity": None,
                "formula_fidelity": None,
                "coverage": None,
                "artifacts": [],
                "verdict": "pending",
                "notes": "",
            }
            for page in sample_pages
        ],
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def score_from_audit_json(path: Path) -> float | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    value = payload.get("overall_score")
    if isinstance(value, int | float):
        return float(value)
    return None


def run_variant(
    *,
    source_pdf: Path,
    source_type: str,
    output_root: Path,
    variant: VariantSpec,
    sample_pages: int,
    audit_seed: int,
    target_score: float,
) -> CandidateResult:
    candidate_dir = output_root / variant.label
    candidate_dir.mkdir(parents=True, exist_ok=True)
    output_path = candidate_dir / f"{source_pdf.stem}.md"
    metadata = pdf_metadata(source_pdf)
    pdf_producer = str(metadata.get("pdf_producer", ""))
    source_sha = sha256_file(source_pdf)

    if variant.parser == "mineru" and not mineru_available():
        return CandidateResult(
            label=variant.label,
            parser=variant.parser,
            state="skipped",
            description=variant.description,
            output_path=output_path,
            audit_bundle_path=None,
            duration_seconds=0.0,
            markdown_size_bytes=0,
            quality_status="skipped",
            quality_warnings=["mineru unavailable"],
            error="mineru unavailable",
        )

    started = time.monotonic()
    error: str | None = None
    try:
        log_event(
            "experiment.variant.started",
            "ok",
            source_pdf=str(source_pdf),
            variant=variant.label,
            parser=variant.parser,
            output_path=str(output_path),
        )
        if variant.parser == "marker":
            convert_with_marker(
                source_path=source_pdf,
                output_path=output_path,
                source_type=source_type,
                sha256=source_sha,
                pdf_producer=pdf_producer,
                disable_ocr=variant.marker_disable_ocr,
                workers=variant.marker_workers,
                low_memory_ocr=variant.marker_low_memory_ocr,
                extra_args=variant.marker_extra_args,
            )
        else:
            convert_with_mineru(
                source_path=source_pdf,
                output_path=output_path,
                source_type=source_type,
                sha256=source_sha,
                method=variant.mineru_method,
                backend=variant.mineru_backend,
                extra_args=variant.mineru_extra_args,
            )
    except Exception as exc:
        error = str(exc)
    duration_seconds = time.monotonic() - started

    if error is not None or not output_path.exists():
        log_event(
            "experiment.variant.failed",
            "error",
            source_pdf=str(source_pdf),
            variant=variant.label,
            parser=variant.parser,
            error=error or "missing output",
            duration_seconds=duration_seconds,
        )
        return CandidateResult(
            label=variant.label,
            parser=variant.parser,
            state="failed",
            description=variant.description,
            output_path=output_path,
            audit_bundle_path=None,
            duration_seconds=duration_seconds,
            markdown_size_bytes=0,
            quality_status="failed",
            quality_warnings=[],
            error=error or "missing output",
        )

    quality = check_markdown_quality(
        output_path,
        math_heavy=source_type.lower() in {"book", "paper"},
        page_count=int(metadata["page_count"]) if metadata.get("page_count") is not None else None,
    )
    bundle_dir = candidate_dir / "visual-audit"
    bundle = prepare_book_audit_bundle(
        source_pdf=source_pdf,
        markdown_path=output_path,
        output_dir=bundle_dir,
        sample_count=sample_pages,
        seed=audit_seed,
    )
    bundle_path = write_prepared_book_bundle(bundle, output_path=bundle_dir / "bundle.json")
    write_audit_template(
        output_path=bundle_dir / "agent-audit.json",
        source_pdf=source_pdf,
        markdown_path=output_path,
        target_score=target_score,
        sample_pages=bundle.sampled_pages,
    )
    result = CandidateResult(
        label=variant.label,
        parser=variant.parser,
        state="ok" if quality.status != "blocked" else "failed",
        description=variant.description,
        output_path=output_path,
        audit_bundle_path=bundle_path,
        duration_seconds=duration_seconds,
        markdown_size_bytes=output_path.stat().st_size,
        quality_status=quality.status,
        quality_warnings=quality.warnings,
        error=None if quality.status != "blocked" else "; ".join(quality.warnings),
    )
    log_event(
        "experiment.variant.finished",
        "ok" if result.state == "ok" else "error",
        source_pdf=str(source_pdf),
        variant=variant.label,
        parser=variant.parser,
        duration_seconds=duration_seconds,
        markdown_size_bytes=result.markdown_size_bytes,
        quality_status=result.quality_status,
        quality_warnings=result.quality_warnings,
    )
    return result


def write_candidate_result(result: CandidateResult, *, output_path: Path) -> Path:
    output_path.write_text(json.dumps(asdict(result), indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return output_path


def write_experiment_manifest(
    *,
    source_pdf: Path,
    output_dir: Path,
    target_score: float,
    variants: list[VariantSpec],
    results: list[CandidateResult],
) -> Path:
    payload = {
        "source_pdf": str(source_pdf),
        "source_sha256": sha256_file(source_pdf),
        "target_score": target_score,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "variants": [asdict(variant) for variant in variants],
        "results": [asdict(result) for result in results],
    }
    path = output_dir / "experiment-manifest.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path


def parser_env_summary() -> dict[str, object]:
    return {
        "marker_timeout_seconds": os.environ.get("RAG_SYNC_MARKER_TIMEOUT_SECONDS", "1200"),
        "mineru_timeout_seconds": os.environ.get("RAG_SYNC_MINERU_TIMEOUT_SECONDS", "1200"),
        "marker_low_memory_ocr": os.environ.get("RAG_SYNC_MARKER_LOW_MEMORY_OCR", "1"),
        "mineru_available": mineru_available(),
    }
