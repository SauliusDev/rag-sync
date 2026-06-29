import json
from pathlib import Path

from rag_sync.db import RagSyncDb
from rag_sync.history import (
    apply_live_glm_ocr_progress,
    estimate_from_history,
    estimate_from_live_progress,
    estimate_job_timing,
    estimate_queue_timing,
    format_eta_seconds,
    size_band_for_bytes,
)
from rag_sync.models import SourceState


def test_estimate_from_live_progress_uses_elapsed_and_progress():
    assert estimate_from_live_progress(elapsed_seconds=600, progress=0.25) == 1800


def test_estimate_from_live_progress_rejects_zero_and_complete_progress():
    assert estimate_from_live_progress(elapsed_seconds=600, progress=0) is None
    assert estimate_from_live_progress(elapsed_seconds=600, progress=1) == 0


def test_apply_live_glm_ocr_progress_uses_rendered_pages_for_running_sync_file(
    tmp_path: Path,
):
    source = tmp_path / "Advanced Portfolio Management - Grinold & Kahn.pdf"
    source.write_text("pdf", encoding="utf-8")
    output_root = tmp_path / "outputs"
    raw_dir = (
        output_root
        / "quant-books"
        / "glm-ocr"
        / ".parser-raw"
        / "glm-ocr"
        / "Advanced_Portfolio_Management_-_Grinold_Kahn-8ce3123c9854"
        / "rendered-pages"
    )
    raw_dir.mkdir(parents=True)
    for page in range(1, 334):
        (raw_dir / f"page_{page:04d}.png").write_bytes(b"png")

    job = {
        "kind": "sync_file",
        "status": "running",
        "profile_name": "quant-books",
        "progress": 0.0,
    }
    source_row = {
        "profile_name": "quant-books",
        "source_path": str(source),
        "extension": "pdf",
        "page_count": 666,
        "artifact": {
            "parser": "marker",
            "output_path": str(
                output_root
                / "quant-books"
                / "marker"
                / "Advanced_Portfolio_Management_-_Grinold_Kahn-8ce3123c9854.md"
            ),
        },
    }

    updated = apply_live_glm_ocr_progress(job, source_row, output_root=output_root)

    assert updated["progress"] == 332 / 666
    assert updated["live_stage"] == "convert"
    assert updated["live_progress_percent"] == 50
    assert updated["live_progress_pages_done"] == 332
    assert updated["live_progress_page_count"] == 666
    assert updated["live_progress_detail"] == "332/666 OCR pages"


def test_estimate_from_history_uses_matching_group_average():
    rows = [
        {
            "profile_name": "quant-books",
            "source_type": "book",
            "parser": "marker",
            "stage": "convert",
            "duration_seconds": 10.0,
        },
        {
            "profile_name": "quant-books",
            "source_type": "book",
            "parser": "marker",
            "stage": "convert",
            "duration_seconds": 20.0,
        },
        {
            "profile_name": "quant-papers",
            "source_type": "paper",
            "parser": "marker",
            "stage": "convert",
            "duration_seconds": 100.0,
        },
    ]

    assert estimate_from_history(
        rows,
        profile_name="quant-books",
        source_type="book",
        parser="marker",
        stage="convert",
    ) == 15


def test_format_eta_seconds_is_compact():
    assert format_eta_seconds(None) == "unknown"
    assert format_eta_seconds(45) == "45s"
    assert format_eta_seconds(125) == "2m"
    assert format_eta_seconds(3700) == "1h 1m"


def _source(
    db: RagSyncDb,
    tmp_path: Path,
    name: str,
    size_bytes: int,
    source_type: str,
    extension: str,
    *,
    page_count: int | None = None,
    pdf_producer: str = "",
) -> int:
    path = tmp_path / name
    path.write_text("x", encoding="utf-8")
    return db.upsert_source_file(
        profile_name="quant-books",
        source_path=str(path),
        source_type=source_type,
        extension=extension,
        sha256=f"sha-{name}",
        size_bytes=size_bytes,
        mtime=1.0,
        state=SourceState.NEW,
        page_count=page_count,
        pdf_producer=pdf_producer,
    )


def test_size_band_for_bytes_uses_expected_buckets():
    assert size_band_for_bytes(500_000) == "0-1MB"
    assert size_band_for_bytes(2_000_000) == "1-10MB"
    assert size_band_for_bytes(20_000_000) == "10-50MB"
    assert size_band_for_bytes(80_000_000) == "50-200MB"
    assert size_band_for_bytes(250_000_000) == "200MB+"


def test_completed_stage_durations_returns_completed_rows_with_source_metadata(
    tmp_path: Path,
):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "book.pdf", 5_000_000, "book", "pdf")
    other_source_id = _source(db, tmp_path, "paper.epub", 7_000_000, "paper", "epub")
    no_run_source_id = _source(db, tmp_path, "notes.md", 1_000_000, "note", "md")

    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=120.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="failed",
        progress=1.0,
        progress_message="failed",
        duration_seconds=999.0,
        error_summary="boom",
    )
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="upload",
        status="completed",
        progress=1.0,
        progress_message="missing duration",
        duration_seconds=None,
        error_summary="",
    )
    other_run_id = db.create_pipeline_run(
        other_source_id,
        "quant-books",
        "paper",
        "mineru",
        "sync_file",
    )
    db.record_stage_event(
        run_id=other_run_id,
        job_id=None,
        source_file_id=other_source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=60.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=None,
        job_id=None,
        source_file_id=no_run_source_id,
        stage="upload",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=30.0,
        error_summary="",
    )

    assert db.completed_stage_durations(limit=3) == [
            {
                "profile_name": "quant-books",
                "stage": "upload",
                "duration_seconds": 30.0,
                "source_type": "note",
                "extension": "md",
                "size_bytes": 1_000_000,
                "page_count": None,
                "pdf_producer": "",
                "parser": None,
            },
            {
                "profile_name": "quant-books",
                "stage": "convert",
                "duration_seconds": 60.0,
                "source_type": "paper",
                "extension": "epub",
                "size_bytes": 7_000_000,
                "page_count": None,
                "pdf_producer": "",
                "parser": "mineru",
            },
            {
                "profile_name": "quant-books",
                "stage": "convert",
                "duration_seconds": 120.0,
                "source_type": "book",
                "extension": "pdf",
                "size_bytes": 5_000_000,
                "page_count": None,
                "pdf_producer": "",
                "parser": "marker",
            },
        ]


def test_completed_stage_durations_prefers_snapshot_metadata_from_event_data_json(
    tmp_path: Path,
):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "snapshot.pdf", 5_000_000, "book", "pdf")
    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")

    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=45.0,
        error_summary="",
        data_json=json.dumps(
            {
                "profile_name": "snapshot-profile",
                "source_type": "snapshot-type",
                "extension": "snapshot-ext",
                "size_bytes": 123456,
                "parser": "snapshot-parser",
            }
        ),
    )

    with db.session() as conn:
        conn.execute(
            """
            UPDATE source_files
            SET profile_name = ?, source_type = ?, extension = ?, size_bytes = ?
            WHERE id = ?
            """,
            ("mutated-profile", "mutated-type", "mutated-ext", 999999, source_id),
        )
        conn.execute(
            """
            UPDATE pipeline_runs
            SET parser = ?
            WHERE id = ?
            """,
            ("mutated-parser", run_id),
        )

    assert db.completed_stage_durations(limit=1) == [
            {
                "profile_name": "snapshot-profile",
                "stage": "convert",
                "duration_seconds": 45.0,
                "source_type": "snapshot-type",
                "extension": "snapshot-ext",
                "size_bytes": 123456,
                "page_count": None,
                "pdf_producer": "",
                "parser": "snapshot-parser",
            }
        ]


def test_completed_stage_durations_falls_back_when_event_data_json_is_invalid(
    tmp_path: Path,
):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "invalid-json.pdf", 2_000_000, "book", "pdf")
    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")

    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=15.0,
        error_summary="",
        data_json="{not valid json",
    )

    assert db.completed_stage_durations(limit=1) == [
            {
                "profile_name": "quant-books",
                "stage": "convert",
                "duration_seconds": 15.0,
                "source_type": "book",
                "extension": "pdf",
                "size_bytes": 2_000_000,
                "page_count": None,
                "pdf_producer": "",
                "parser": "marker",
            }
        ]


def test_estimate_job_timing_prefers_specific_group_then_falls_back(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "book.pdf", 5_000_000, "book", "pdf")
    other_source_id = _source(db, tmp_path, "fallback.epub", 5_000_000, "book", "epub")

    specific_run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")
    db.record_stage_event(
        run_id=specific_run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=120.0,
        error_summary="",
    )
    fallback_run_id = db.create_pipeline_run(
        other_source_id,
        "quant-books",
        "book",
        "marker",
        "sync_file",
    )
    db.record_stage_event(
        run_id=fallback_run_id,
        job_id=None,
        source_file_id=other_source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=240.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=specific_run_id,
        job_id=None,
        source_file_id=source_id,
        stage="upload",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=30.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=specific_run_id,
        job_id=None,
        source_file_id=source_id,
        stage="parse",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=15.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=fallback_run_id,
        job_id=None,
        source_file_id=other_source_id,
        stage="upload",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=45.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=fallback_run_id,
        job_id=None,
        source_file_id=other_source_id,
        stage="parse",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=20.0,
        error_summary="",
    )

    queued_job = {
        "id": 1,
        "kind": "sync_file",
        "status": "queued",
        "source_file_id": source_id,
        "progress": 0.0,
    }
    source_row = next(row for row in db.list_source_files() if int(row["id"]) == source_id)

    estimate = estimate_job_timing(db, queued_job, source_row)

    assert estimate["eta_seconds"] == 165
    assert estimate["confidence"] == "low"
    assert "convert" in estimate["timing_basis"]

    fallback_source_id = _source(db, tmp_path, "fallback-target.docx", 5_000_000, "book", "docx")
    fallback_source_row = next(
        row for row in db.list_source_files() if int(row["id"]) == fallback_source_id
    )
    fallback_job = {
        "id": 2,
        "kind": "sync_file",
        "status": "queued",
        "source_file_id": fallback_source_id,
        "progress": 0.0,
    }

    fallback_estimate = estimate_job_timing(db, fallback_job, fallback_source_row)

    assert fallback_estimate["eta_seconds"] == 236
    assert fallback_estimate["confidence"] == "low"
    assert "convert" in fallback_estimate["timing_basis"]


def test_estimate_job_timing_for_sync_file_uses_remaining_stage_path(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "workflow.pdf", 5_000_000, "book", "pdf")
    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")

    for stage, seconds in [("convert", 120.0), ("upload", 30.0), ("parse", 15.0)]:
        db.record_stage_event(
            run_id=run_id,
            job_id=None,
            source_file_id=source_id,
            stage=stage,
            status="completed",
            progress=1.0,
            progress_message="done",
            duration_seconds=seconds,
            error_summary="",
        )

    before_convert_row = next(row for row in db.list_file_summaries() if int(row["id"]) == source_id)
    before_convert_job = {
        "id": 1,
        "kind": "sync_file",
        "status": "queued",
        "source_file_id": source_id,
        "progress": 0.0,
    }

    before_convert_estimate = estimate_job_timing(db, before_convert_job, before_convert_row)

    assert before_convert_estimate["eta_seconds"] == 165
    assert before_convert_estimate["confidence"] == "low"

    db.add_artifact(source_id, "marker", str(tmp_path / "workflow.md"), "artifact-sha", "ok", "[]")
    after_convert_row = next(row for row in db.list_file_summaries() if int(row["id"]) == source_id)

    after_convert_estimate = estimate_job_timing(db, before_convert_job, after_convert_row)

    assert after_convert_estimate["eta_seconds"] == 165

    db.upsert_ragflow_document(
        source_id,
        dataset_id="dataset",
        dataset_name="dataset",
        document_id="doc",
        document_name="workflow.md",
        upload_status="uploaded",
        parse_status="parsing",
    )
    after_upload_row = next(row for row in db.list_file_summaries() if int(row["id"]) == source_id)

    after_upload_estimate = estimate_job_timing(db, before_convert_job, after_upload_row)

    assert after_upload_estimate["eta_seconds"] == 165


def test_estimate_job_timing_for_sync_file_requires_successful_upload_before_parse_only(
    tmp_path: Path,
):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "upload-retry.pdf", 5_000_000, "book", "pdf")
    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")

    for stage, seconds in [("convert", 120.0), ("upload", 30.0), ("parse", 15.0)]:
        db.record_stage_event(
            run_id=run_id,
            job_id=None,
            source_file_id=source_id,
            stage=stage,
            status="completed",
            progress=1.0,
            progress_message="done",
            duration_seconds=seconds,
            error_summary="",
        )

    db.add_artifact(source_id, "marker", str(tmp_path / "upload-retry.md"), "artifact-sha", "ok", "[]")
    db.upsert_ragflow_document(
        source_id,
        dataset_id="dataset",
        dataset_name="dataset",
        document_id="doc",
        document_name="upload-retry.md",
        upload_status="failed",
        parse_status="not_started",
    )
    row = next(row for row in db.list_file_summaries() if int(row["id"]) == source_id)
    job = {
        "id": 1,
        "kind": "sync_file",
        "status": "queued",
        "source_file_id": source_id,
        "progress": 0.0,
    }

    estimate = estimate_job_timing(db, job, row)

    assert estimate["eta_seconds"] == 165
    assert estimate["timing_basis"] == (
        "convert+book+pdf+1-10MB -> upload+book+pdf+1-10MB -> parse+book+pdf+1-10MB"
    )


def test_estimate_job_timing_uses_artifact_parser_for_post_convert_eta(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    marker_source_id = _source(db, tmp_path, "marker.pdf", 5_000_000, "book", "pdf")
    mineru_source_id = _source(db, tmp_path, "mineru.pdf", 5_000_000, "book", "pdf")

    marker_run_id = db.create_pipeline_run(
        marker_source_id,
        "quant-books",
        "book",
        "marker",
        "sync_file",
    )
    mineru_run_id = db.create_pipeline_run(
        mineru_source_id,
        "quant-books",
        "book",
        "mineru",
        "sync_file",
    )

    for stage, seconds in [("upload", 30.0), ("parse", 15.0)]:
        db.record_stage_event(
            run_id=marker_run_id,
            job_id=None,
            source_file_id=marker_source_id,
            stage=stage,
            status="completed",
            progress=1.0,
            progress_message="done",
            duration_seconds=seconds,
            error_summary="",
        )
    for stage, seconds in [("upload", 90.0), ("parse", 45.0)]:
        db.record_stage_event(
            run_id=mineru_run_id,
            job_id=None,
            source_file_id=mineru_source_id,
            stage=stage,
            status="completed",
            progress=1.0,
            progress_message="done",
            duration_seconds=seconds,
            error_summary="",
        )

    db.add_artifact(
        mineru_source_id,
        "mineru",
        str(tmp_path / "mineru.md"),
        "artifact-sha",
        "ok",
        "[]",
    )
    row = next(row for row in db.list_file_summaries() if int(row["id"]) == mineru_source_id)
    job = {
        "id": 1,
        "kind": "sync_file",
        "status": "queued",
        "source_file_id": mineru_source_id,
        "progress": 0.0,
    }

    estimate = estimate_job_timing(db, job, row)

    assert estimate["eta_seconds"] == 128
    assert estimate["timing_basis"] == (
        "recent_median -> upload+book+pdf+1-10MB -> parse+book+pdf+1-10MB"
    )


def test_estimate_queue_timing_uses_broad_fallback_for_sparse_history(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_a = _source(db, tmp_path, "known.pdf", 5_000_000, "book", "pdf")
    source_b = _source(db, tmp_path, "sparse.docx", 5_000_000, "book", "docx")
    run_id = db.create_pipeline_run(source_a, "quant-books", "book", "marker", "sync_file")

    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_a,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=120.0,
        error_summary="",
    )

    jobs = [
        {"id": 1, "kind": "sync_file", "status": "queued", "source_file_id": source_a, "progress": 0.0},
        {"id": 2, "kind": "parse", "status": "queued", "source_file_id": source_b, "progress": 0.0},
    ]
    files = {int(row["id"]): row for row in db.list_file_summaries()}

    estimate = estimate_queue_timing(db, jobs, files)

    assert estimate["seconds"] == 480
    assert estimate["label"] == "8m remaining"
    assert estimate["confidence"] == "low"


def test_estimate_queue_timing_uses_broad_fallback_for_jobs_without_source_rows(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "known.pdf", 5_000_000, "book", "pdf")
    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")
    for stage, seconds in [("convert", 120.0), ("upload", 30.0), ("parse", 15.0)]:
        db.record_stage_event(
            run_id=run_id,
            job_id=None,
            source_file_id=source_id,
            stage=stage,
            status="completed",
            progress=1.0,
            progress_message="done",
            duration_seconds=seconds,
            error_summary="",
        )

    jobs = [
        {"id": 1, "kind": "sync_file", "status": "queued", "source_file_id": source_id, "progress": 0.0},
        {"id": 2, "kind": "sync_file", "status": "queued", "source_file_id": 9999, "progress": 0.0},
    ]
    files = {int(row["id"]): row for row in db.list_file_summaries()}

    estimate = estimate_queue_timing(db, jobs, files, now="2026-06-26T10:00:00")

    assert estimate["seconds"] == 195
    assert estimate["label"] == "3m remaining"
    assert estimate["confidence"] == "low"
    assert estimate["estimated_finish_at"] == "2026-06-26T10:03:15"


def test_estimate_queue_timing_sums_active_and_queued_jobs(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_a = _source(db, tmp_path, "a.pdf", 5_000_000, "book", "pdf")
    source_b = _source(db, tmp_path, "b.pdf", 5_000_000, "book", "pdf")

    run_id = db.create_pipeline_run(source_a, "quant-books", "book", "marker", "sync_file")
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_a,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=60.0,
        error_summary="",
    )

    jobs = [
        {"id": 1, "kind": "sync_file", "status": "running", "source_file_id": source_a, "progress": 0.0},
        {"id": 2, "kind": "sync_file", "status": "queued", "source_file_id": source_b, "progress": 0.0},
    ]
    files = {int(row["id"]): row for row in db.list_source_files()}

    estimate = estimate_queue_timing(db, jobs, files)

    assert estimate["seconds"] == 360
    assert estimate["label"] == "6m remaining"
    assert estimate["throughput_label"] == "recent median 1m/file"


def test_quant_paper_pdf_eta_uses_glm_ocr_profile_default(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = db.upsert_source_file(
        "quant-papers",
        "/tmp/paper.pdf",
        "paper",
        "pdf",
        "abc",
        1_000_000,
        1.0,
        SourceState.NEW,
        page_count=10,
    )
    run_id = db.create_pipeline_run(
        source_id,
        "quant-papers",
        "paper",
        "glm-ocr",
        "test",
    )
    db.record_stage_event(
        run_id,
        None,
        source_id,
        "convert",
        "completed",
        1,
        "",
        30,
        "",
    )
    queued = db.create_job("sync_file", source_file_id=source_id, profile_name="quant-papers")
    files = {source_id: db.list_file_summaries(profile_names={"quant-papers"})[0]}
    jobs = [job for job in db.list_jobs() if int(job["id"]) == queued]

    estimate = estimate_job_timing(db, jobs[0], files[source_id])

    assert estimate["eta_seconds"] >= 30
    assert "glm-ocr" in estimate["timing_basis"]


def test_estimate_job_timing_uses_live_progress_for_running_job(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "running.pdf", 5_000_000, "book", "pdf")

    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=120.0,
        error_summary="",
    )

    running_job = {
        "id": 1,
        "kind": "sync_file",
        "status": "running",
        "source_file_id": source_id,
        "progress": 0.25,
        "started_at": "2026-06-26 10:00:00",
    }
    source_row = next(row for row in db.list_source_files() if int(row["id"]) == source_id)

    estimate = estimate_job_timing(
        db,
        running_job,
        source_row,
        now="2026-06-26 10:01:00",
    )

    assert estimate["eta_seconds"] == 420
    assert estimate["eta_label"] == "7m remaining"


def test_estimate_job_timing_uses_glm_ocr_page_rate_for_live_progress(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    completed_source = _source(
        db,
        tmp_path,
        "completed.pdf",
        2_000_000,
        "book",
        "pdf",
        page_count=100,
    )
    running_source = _source(
        db,
        tmp_path,
        "running.pdf",
        2_000_000,
        "book",
        "pdf",
        page_count=100,
    )
    run_id = db.create_pipeline_run(completed_source, "quant-books", "book", "glm-ocr", "sync_file")
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=completed_source,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=1_000.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=completed_source,
        stage="upload",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=20.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=completed_source,
        stage="parse",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=30.0,
        error_summary="",
    )
    running_job = {
        "id": 1,
        "kind": "sync_file",
        "status": "running",
        "source_file_id": running_source,
        "started_at": "2026-06-26 10:00:00",
        "progress": 0.5,
        "live_stage": "convert",
        "live_progress_pages_done": 50,
        "live_progress_page_count": 100,
        "live_progress_percent": 50,
    }
    running_row = next(row for row in db.list_source_files() if int(row["id"]) == running_source)

    estimate = estimate_job_timing(
        db,
        running_job,
        running_row,
        now="2026-06-26 10:01:00",
    )

    assert estimate["eta_seconds"] == 550
    assert estimate["eta_label"] == "9m remaining"
    assert estimate["timing_basis"] == (
        "convert+book+pdf+page-rate+glm-ocr-live-pages -> "
        "upload+book+pdf+26-100p+1-10MB+glm-ocr -> "
        "parse+book+pdf+26-100p+1-10MB+glm-ocr"
    )


def test_live_glm_ocr_eta_ignores_stale_marker_artifact(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    completed_source = _source(
        db,
        tmp_path,
        "completed.pdf",
        2_000_000,
        "book",
        "pdf",
        page_count=100,
    )
    running_source = _source(
        db,
        tmp_path,
        "running.pdf",
        2_000_000,
        "book",
        "pdf",
        page_count=100,
    )
    stale_artifact = tmp_path / "old-marker.md"
    stale_artifact.write_text("# old\n", encoding="utf-8")
    db.add_artifact(
        source_file_id=running_source,
        parser="marker",
        output_path=str(stale_artifact),
        output_sha256="abc",
        quality_status="clean",
        warnings_json="[]",
    )
    run_id = db.create_pipeline_run(completed_source, "quant-books", "book", "glm-ocr", "sync_file")
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=completed_source,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=1_000.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=completed_source,
        stage="upload",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=20.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=completed_source,
        stage="parse",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=30.0,
        error_summary="",
    )
    running_job = {
        "id": 1,
        "kind": "sync_file",
        "status": "running",
        "source_file_id": running_source,
        "started_at": "2026-06-26 10:00:00",
        "progress": 0.5,
        "live_stage": "convert",
        "live_progress_pages_done": 50,
        "live_progress_page_count": 100,
        "live_progress_percent": 50,
    }
    running_row = db.list_file_summaries(profile_names={"quant-books"})[1]

    estimate = estimate_job_timing(
        db,
        running_job,
        running_row,
        now="2026-06-26 10:01:00",
    )

    assert estimate["timing_basis"].startswith("convert+book+pdf+page-rate+glm-ocr-live-pages")
    assert estimate["eta_seconds"] == 550


def test_estimate_queue_timing_reports_unknown_when_no_job_is_estimable(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()

    estimate = estimate_queue_timing(
        db,
        [{"id": 1, "kind": "retrieval_test", "status": "queued", "progress": 0.0}],
        {},
    )

    assert estimate["seconds"] is None
    assert estimate["label"] == "unknown"
    assert estimate["confidence"] == "estimating"


def test_estimate_job_timing_marks_live_progress_provenance(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "live.pdf", 5_000_000, "book", "pdf")

    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=120.0,
        error_summary="",
    )

    running_job = {
        "id": 1,
        "kind": "sync_file",
        "status": "running",
        "source_file_id": source_id,
        "progress": 0.25,
        "started_at": "2026-06-26 10:00:00",
    }
    source_row = next(row for row in db.list_source_files() if int(row["id"]) == source_id)

    estimate = estimate_job_timing(
        db,
        running_job,
        source_row,
        now="2026-06-26 10:01:00",
    )

    assert estimate["eta_seconds"] == 420
    assert estimate["confidence"] == "low"
    assert estimate["timing_basis"] == "live_progress -> recent_median -> recent_median"


def test_estimate_job_timing_for_running_job_without_live_progress_subtracts_elapsed(
    tmp_path: Path,
):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "elapsed.pdf", 5_000_000, "book", "pdf")

    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")
    for seconds in (120.0, 180.0, 240.0):
        db.record_stage_event(
            run_id=run_id,
            job_id=None,
            source_file_id=source_id,
            stage="convert",
            status="completed",
            progress=1.0,
            progress_message="done",
            duration_seconds=seconds,
            error_summary="",
        )

    running_job = {
        "id": 1,
        "kind": "sync_file",
        "status": "running",
        "source_file_id": source_id,
        "progress": 0.0,
        "started_at": "2026-06-26 10:00:00",
    }
    source_row = next(row for row in db.list_source_files() if int(row["id"]) == source_id)

    estimate = estimate_job_timing(
        db,
        running_job,
        source_row,
        now="2026-06-26 10:02:00",
    )

    assert estimate["eta_seconds"] == 420
    assert estimate["eta_label"] == "7m remaining"
    assert estimate["progress_percent"] == 67


def test_estimate_job_timing_only_applies_elapsed_heuristic_to_current_stage(
    tmp_path: Path,
):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "multi-stage.pdf", 5_000_000, "book", "pdf")

    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=180.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="upload",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=60.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="parse",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=90.0,
        error_summary="",
    )

    running_job = {
        "id": 1,
        "kind": "sync_file",
        "status": "running",
        "source_file_id": source_id,
        "progress": 0.0,
        "started_at": "2026-06-26 10:00:00",
    }
    source_row = next(row for row in db.list_source_files() if int(row["id"]) == source_id)

    estimate = estimate_job_timing(
        db,
        running_job,
        source_row,
        now="2026-06-26 10:02:00",
    )

    assert estimate["eta_seconds"] == 210
    assert estimate["eta_label"] == "3m remaining"
    assert estimate["timing_basis"] == (
        "convert+book+pdf+1-10MB-elapsed"
        " -> upload+book+pdf+1-10MB"
        " -> parse+book+pdf+1-10MB"
    )


def test_estimate_job_timing_uses_page_count_for_pdf_convert_stage(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_a = _source(db, tmp_path, "a.pdf", 2_000_000, "book", "pdf", page_count=100)
    source_b = _source(db, tmp_path, "b.pdf", 3_000_000, "book", "pdf", page_count=200)
    target = _source(db, tmp_path, "target.pdf", 2_500_000, "book", "pdf", page_count=150)

    run_a = db.create_pipeline_run(source_a, "quant-books", "book", "marker", "sync_file")
    run_b = db.create_pipeline_run(source_b, "quant-books", "book", "marker", "sync_file")

    db.record_stage_event(
        run_id=run_a,
        job_id=None,
        source_file_id=source_a,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=100.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=run_b,
        job_id=None,
        source_file_id=source_b,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=200.0,
        error_summary="",
    )

    target_row = next(row for row in db.list_source_files() if int(row["id"]) == target)
    queued_job = {
        "id": 1,
        "kind": "convert",
        "status": "queued",
        "source_file_id": target,
        "progress": 0.0,
    }

    estimate = estimate_job_timing(db, queued_job, target_row)

    assert estimate["eta_seconds"] == 150
    assert estimate["timing_basis"].startswith("convert+book+pdf+page-rate+generic")


def test_queued_sync_file_eta_includes_convert_even_with_existing_artifact(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "rerun.pdf", 2_000_000, "book", "pdf", page_count=100)
    artifact = tmp_path / "old.md"
    artifact.write_text("# old\n", encoding="utf-8")
    db.add_artifact(
        source_file_id=source_id,
        parser="marker",
        output_path=str(artifact),
        output_sha256="abc",
        quality_status="clean",
        warnings_json="[]",
    )
    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "glm-ocr", "sync_file")
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=200.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="upload",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=20.0,
        error_summary="",
    )
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="parse",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=30.0,
        error_summary="",
    )
    queued_job = {
        "id": 1,
        "kind": "sync_file",
        "status": "queued",
        "source_file_id": source_id,
        "progress": 0.0,
    }
    source_row = next(row for row in db.list_source_files() if int(row["id"]) == source_id)

    estimate = estimate_job_timing(db, queued_job, source_row)

    assert estimate["eta_seconds"] == 250
    assert estimate["timing_basis"].startswith("convert+book+pdf+page-rate")


def test_estimate_job_timing_caps_heuristic_progress_for_clearscan_marker_jobs(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(
        db,
        tmp_path,
        "shreve.pdf",
        7_800_000,
        "book",
        "pdf",
        page_count=570,
        pdf_producer="Adobe Acrobat 10.01 Paper Capture Plug-in with ClearScan",
    )

    for seconds in (180.0, 210.0, 240.0):
        run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")
        db.record_stage_event(
            run_id=run_id,
            job_id=None,
            source_file_id=source_id,
            stage="convert",
            status="completed",
            progress=1.0,
            progress_message="done",
            duration_seconds=seconds,
            error_summary="",
        )

    source_row = next(row for row in db.list_source_files() if int(row["id"]) == source_id)
    running_job = {
        "id": 1,
        "kind": "sync_file",
        "status": "running",
        "source_file_id": source_id,
        "progress": 0.0,
        "started_at": "2026-06-26 10:00:00",
    }

    estimate = estimate_job_timing(
        db,
        running_job,
        source_row,
        now="2026-06-26 10:03:55",
    )

    assert estimate["confidence"] == "low"
    assert estimate["progress_percent"] == 70


def test_estimate_job_timing_marks_badly_overrun_running_job_unknown(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "stalled.pdf", 5_000_000, "book", "pdf")

    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")
    for seconds in (60.0, 75.0, 90.0):
        db.record_stage_event(
            run_id=run_id,
            job_id=None,
            source_file_id=source_id,
            stage="convert",
            status="completed",
            progress=1.0,
            progress_message="done",
            duration_seconds=seconds,
            error_summary="",
        )

    running_job = {
        "id": 1,
        "kind": "sync_file",
        "status": "running",
        "source_file_id": source_id,
        "progress": 0.0,
        "started_at": "2026-06-26 10:00:00",
    }
    source_row = next(row for row in db.list_source_files() if int(row["id"]) == source_id)

    estimate = estimate_job_timing(
        db,
        running_job,
        source_row,
        now="2026-06-26 10:20:00",
    )

    assert estimate["eta_seconds"] is None
    assert estimate["eta_label"] == "unknown"
    assert estimate["timing_basis"] == "stalled"
    assert estimate["progress_percent"] is None


def test_estimate_queue_timing_passes_shared_now_to_running_jobs(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_a = _source(db, tmp_path, "queue-running.pdf", 5_000_000, "book", "pdf")
    source_b = _source(db, tmp_path, "queue-queued.pdf", 5_000_000, "book", "pdf")

    run_id = db.create_pipeline_run(source_a, "quant-books", "book", "marker", "sync_file")
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_a,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=120.0,
        error_summary="",
    )

    jobs = [
        {
            "id": 1,
            "kind": "sync_file",
            "status": "running",
            "source_file_id": source_a,
            "progress": 0.25,
            "started_at": "2026-06-26 10:00:00",
        },
        {
            "id": 2,
            "kind": "sync_file",
            "status": "queued",
            "source_file_id": source_b,
            "progress": 0.0,
        },
    ]
    files = {int(row["id"]): row for row in db.list_source_files()}

    estimate = estimate_queue_timing(
        db,
        jobs,
        files,
        now="2026-06-26 10:01:00",
    )

    assert estimate["seconds"] == 780
    assert estimate["label"] == "13m remaining"


def test_estimate_queue_timing_includes_glm_ocr_api_cost_from_usage_history(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    history_source = _source(db, tmp_path, "history.pdf", 5_000_000, "book", "pdf", page_count=100)
    queued_source = _source(db, tmp_path, "queued.pdf", 6_000_000, "book", "pdf", page_count=200)

    db.record_usage_event(
        provider="z-ai",
        service="glm-ocr",
        model="glm-ocr",
        source_file_id=history_source,
        tokens=50_000,
        cost_usd=0.0015,
        metadata={"page_count": 100},
    )
    run_id = db.create_pipeline_run(history_source, "quant-books", "book", "glm-ocr", "sync_file")
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=history_source,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=100.0,
        error_summary="",
    )

    jobs = [
        {
            "id": 1,
            "kind": "sync_file",
            "status": "queued",
            "source_file_id": queued_source,
            "progress": 0.0,
        },
    ]
    files = {int(row["id"]): row for row in db.list_source_files()}

    estimate = estimate_queue_timing(db, jobs, files)

    assert estimate["estimated_api_tokens"] == 100_000
    assert estimate["estimated_api_cost_usd"] == 0.003
    assert estimate["estimated_api_cost_label"] == "$0.003000 estimated GLM OCR"
    assert estimate["api_cost_basis"] == "z-ai glm-ocr median 500 tokens/page"


def test_estimate_queue_timing_marks_mixed_known_and_unknown_queue_partial(tmp_path: Path):
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    source_id = _source(db, tmp_path, "known.pdf", 5_000_000, "book", "pdf")

    run_id = db.create_pipeline_run(source_id, "quant-books", "book", "marker", "sync_file")
    db.record_stage_event(
        run_id=run_id,
        job_id=None,
        source_file_id=source_id,
        stage="convert",
        status="completed",
        progress=1.0,
        progress_message="done",
        duration_seconds=120.0,
        error_summary="",
    )

    jobs = [
        {"id": 1, "kind": "sync_file", "status": "queued", "source_file_id": source_id, "progress": 0.0},
        {"id": 2, "kind": "retrieval_test", "status": "queued", "progress": 0.0},
    ]
    files = {int(row["id"]): row for row in db.list_source_files()}

    estimate = estimate_queue_timing(db, jobs, files)

    assert estimate["seconds"] == 480
    assert estimate["label"] == "8m remaining"
    assert estimate["confidence"] == "low"
