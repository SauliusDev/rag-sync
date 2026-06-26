from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rag_sync.models import ImportValidationStatus, SourceState

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_name TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_type TEXT NOT NULL,
  extension TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  page_count INTEGER,
  pdf_producer TEXT NOT NULL DEFAULT '',
  mtime REAL NOT NULL,
  state TEXT NOT NULL,
  included INTEGER NOT NULL DEFAULT 1,
  priority INTEGER NOT NULL DEFAULT 0,
  tags TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(profile_name, source_path)
);

CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file_id INTEGER NOT NULL,
  parser TEXT NOT NULL,
  output_path TEXT NOT NULL,
  output_sha256 TEXT NOT NULL,
  quality_status TEXT NOT NULL,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(source_file_id) REFERENCES source_files(id)
);

CREATE TABLE IF NOT EXISTS ragflow_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file_id INTEGER NOT NULL UNIQUE,
  dataset_id TEXT NOT NULL,
  dataset_name TEXT NOT NULL,
  document_id TEXT NOT NULL,
  document_name TEXT NOT NULL,
  upload_status TEXT NOT NULL,
  parse_status TEXT NOT NULL,
  chunk_count INTEGER,
  token_count INTEGER,
  last_synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(source_file_id) REFERENCES source_files(id)
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  profile_name TEXT,
  source_file_id INTEGER,
  started_at TEXT,
  finished_at TEXT,
  progress REAL NOT NULL DEFAULT 0,
  error_summary TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(source_file_id) REFERENCES source_files(id)
);

CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  level TEXT NOT NULL,
  message TEXT NOT NULL,
  data_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file_id INTEGER NOT NULL,
  profile_name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  parser TEXT NOT NULL,
  trigger TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  error_summary TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(source_file_id) REFERENCES source_files(id)
);

CREATE TABLE IF NOT EXISTS pipeline_stage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER,
  job_id INTEGER,
  source_file_id INTEGER NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  progress REAL NOT NULL DEFAULT 0,
  progress_message TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  duration_seconds REAL,
  error_summary TEXT NOT NULL DEFAULT '',
  data_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(run_id) REFERENCES pipeline_runs(id),
  FOREIGN KEY(job_id) REFERENCES jobs(id),
  FOREIGN KEY(source_file_id) REFERENCES source_files(id)
);

CREATE TABLE IF NOT EXISTS batch_imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL UNIQUE,
  manifest_path TEXT NOT NULL,
  profile_name TEXT NOT NULL,
  parser TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS batch_import_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_import_id INTEGER NOT NULL,
  source_file_id INTEGER,
  source_relpath TEXT NOT NULL,
  manifest_source_sha256 TEXT NOT NULL,
  local_source_sha256 TEXT NOT NULL DEFAULT '',
  markdown_path TEXT NOT NULL,
  markdown_sha256 TEXT NOT NULL,
  validation_status TEXT NOT NULL,
  import_mode TEXT NOT NULL DEFAULT 'strict',
  override_reason TEXT NOT NULL DEFAULT '',
  imported INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(batch_import_id) REFERENCES batch_imports(id),
  FOREIGN KEY(source_file_id) REFERENCES source_files(id)
);
"""


class RagSyncDb:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._worker_lock_fd: int | None = None
        self._worker_lock_owner: str | None = None

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def acquire_worker_lock(self, owner: str) -> bool:
        if self._worker_lock_fd is not None:
            return self._worker_lock_owner == owner
        lock_path = self.path.with_name(f"{self.path.name}.worker.lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, f"{owner}\n".encode("utf-8"))
        self._worker_lock_fd = fd
        self._worker_lock_owner = owner
        return True

    def release_worker_lock(self, owner: str) -> None:
        if self._worker_lock_fd is None or self._worker_lock_owner != owner:
            return
        fd = self._worker_lock_fd
        self._worker_lock_fd = None
        self._worker_lock_owner = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.session() as conn:
            conn.executescript(SCHEMA)
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(source_files)").fetchall()
            }
            if "page_count" not in columns:
                conn.execute("ALTER TABLE source_files ADD COLUMN page_count INTEGER")
            if "pdf_producer" not in columns:
                conn.execute(
                    "ALTER TABLE source_files ADD COLUMN pdf_producer TEXT NOT NULL DEFAULT ''"
                )

    def upsert_source_file(
        self,
        profile_name: str,
        source_path: str,
        source_type: str,
        extension: str,
        sha256: str,
        size_bytes: int,
        mtime: float,
        state: SourceState,
        page_count: int | None = None,
        pdf_producer: str = "",
    ) -> int:
        with self.session() as conn:
            row = conn.execute(
                """
                INSERT INTO source_files (
                  profile_name, source_path, source_type, extension, sha256,
                  size_bytes, page_count, pdf_producer, mtime, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_name, source_path) DO UPDATE SET
                  source_type = excluded.source_type,
                  extension = excluded.extension,
                  sha256 = excluded.sha256,
                  size_bytes = excluded.size_bytes,
                  page_count = excluded.page_count,
                  pdf_producer = excluded.pdf_producer,
                  mtime = excluded.mtime,
                  state = excluded.state,
                  updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                (
                    profile_name,
                    source_path,
                    source_type,
                    extension,
                    sha256,
                    size_bytes,
                    page_count,
                    pdf_producer,
                    mtime,
                    state.value,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("source file upsert did not return an id")
            return int(row["id"])

    def existing_hashes(self, profile_name: str) -> dict[str, str]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT source_path, sha256 FROM source_files WHERE profile_name = ?",
                (profile_name,),
            ).fetchall()
            return {str(row["source_path"]): str(row["sha256"]) for row in rows}

    def update_source_pdf_metadata(
        self,
        source_file_id: int,
        *,
        page_count: int | None,
        pdf_producer: str,
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE source_files
                SET page_count = ?, pdf_producer = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (page_count, pdf_producer, source_file_id),
            )

    def mark_missing_absent_paths(self, profile_name: str, seen_paths: set[str]) -> None:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT id, source_path FROM source_files WHERE profile_name = ?",
                (profile_name,),
            ).fetchall()
            for row in rows:
                if row["source_path"] not in seen_paths:
                    conn.execute(
                        """
                        UPDATE source_files
                        SET state = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (SourceState.MISSING.value, row["id"]),
                    )

    def list_source_files(self, profile_names: set[str] | None = None) -> list[dict[str, Any]]:
        with self.session() as conn:
            if profile_names is None:
                rows = conn.execute(
                    "SELECT * FROM source_files ORDER BY source_path"
                ).fetchall()
            else:
                if not profile_names:
                    return []
                placeholders = ",".join("?" for _ in profile_names)
                rows = conn.execute(
                    f"""
                    SELECT * FROM source_files
                    WHERE profile_name IN ({placeholders})
                    ORDER BY source_path
                    """,
                    tuple(sorted(profile_names)),
                ).fetchall()
            return [dict(row) for row in rows]

    def list_file_summaries(self, profile_names: set[str] | None = None) -> list[dict[str, Any]]:
        files = self.list_source_files(profile_names=profile_names)
        with self.session() as conn:
            for file_row in files:
                source_file_id = int(file_row["id"])
                artifact = conn.execute(
                    """
                    SELECT *
                    FROM artifacts
                    WHERE source_file_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (source_file_id,),
                ).fetchone()
                ragflow = conn.execute(
                    "SELECT * FROM ragflow_documents WHERE source_file_id = ?",
                    (source_file_id,),
                ).fetchone()
                active_job = conn.execute(
                    """
                    SELECT *
                    FROM jobs
                    WHERE source_file_id = ? AND status IN ('queued', 'running')
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (source_file_id,),
                ).fetchone()
                file_row["artifact"] = dict(artifact) if artifact is not None else None
                file_row["ragflow"] = dict(ragflow) if ragflow is not None else None
                file_row["job"] = dict(active_job) if active_job is not None else None
        return files

    def cancel_jobs_for_missing_profiles(self, valid_profile_names: set[str]) -> int:
        if not valid_profile_names:
            return 0
        placeholders = ",".join("?" for _ in valid_profile_names)
        with self.session() as conn:
            rows = conn.execute(
                f"""
                SELECT id FROM jobs
                WHERE status = 'queued'
                  AND profile_name IS NOT NULL
                  AND profile_name NOT IN ({placeholders})
                """,
                tuple(sorted(valid_profile_names)),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'canceled',
                        finished_at = CURRENT_TIMESTAMP,
                        error_summary = 'profile no longer configured'
                    WHERE id = ?
                    """,
                    (int(row["id"]),),
                )
            return len(rows)

    def requeue_running_jobs(self) -> int:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'running'
                """
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued',
                        started_at = NULL,
                        progress = 0,
                        error_summary = 'worker restarted before job finished'
                    WHERE id = ?
                    """,
                    (int(row["id"]),),
                )
            return len(rows)

    def create_pipeline_run(
        self,
        source_file_id: int,
        profile_name: str,
        source_type: str,
        parser: str,
        trigger: str,
    ) -> int:
        with self.session() as conn:
            row = conn.execute(
                """
                INSERT INTO pipeline_runs (
                  source_file_id, profile_name, source_type, parser, trigger
                ) VALUES (?, ?, ?, ?, ?)
                RETURNING id
                """,
                (source_file_id, profile_name, source_type, parser, trigger),
            ).fetchone()
            if row is None:
                raise RuntimeError("pipeline run insert did not return an id")
            return int(row["id"])

    def finish_pipeline_run(self, run_id: int, status: str, error_summary: str = "") -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = ?, finished_at = CURRENT_TIMESTAMP, error_summary = ?
                WHERE id = ?
                """,
                (status, error_summary, run_id),
            )

    def update_pipeline_run_parser(self, run_id: int, parser: str) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET parser = ?
                WHERE id = ?
                """,
                (parser, run_id),
            )

    def record_stage_event(
        self,
        run_id: int | None,
        job_id: int | None,
        source_file_id: int,
        stage: str,
        status: str,
        progress: float,
        progress_message: str,
        duration_seconds: float | None,
        error_summary: str,
        data_json: str = "{}",
    ) -> int:
        with self.session() as conn:
            row = conn.execute(
                """
                INSERT INTO pipeline_stage_events (
                  run_id, job_id, source_file_id, stage, status, progress,
                  progress_message, finished_at, duration_seconds, error_summary, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
                RETURNING id
                """,
                (
                    run_id,
                    job_id,
                    source_file_id,
                    stage,
                    status,
                    progress,
                    progress_message,
                    duration_seconds,
                    error_summary,
                    data_json,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("pipeline stage event insert did not return an id")
            return int(row["id"])

    def recent_stage_events(self, source_file_id: int, limit: int = 20) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM pipeline_stage_events
                WHERE source_file_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (source_file_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def completed_stage_durations(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT
                  COALESCE(
                    CASE
                      WHEN json_valid(pipeline_stage_events.data_json)
                      THEN json_extract(pipeline_stage_events.data_json, '$.profile_name')
                    END,
                    source_files.profile_name
                  ) AS profile_name,
                  pipeline_stage_events.stage,
                  pipeline_stage_events.duration_seconds,
                  COALESCE(
                    CASE
                      WHEN json_valid(pipeline_stage_events.data_json)
                      THEN json_extract(pipeline_stage_events.data_json, '$.source_type')
                    END,
                    source_files.source_type
                  ) AS source_type,
                  COALESCE(
                    CASE
                      WHEN json_valid(pipeline_stage_events.data_json)
                      THEN json_extract(pipeline_stage_events.data_json, '$.extension')
                    END,
                    source_files.extension
                  ) AS extension,
                  COALESCE(
                    CASE
                      WHEN json_valid(pipeline_stage_events.data_json)
                      THEN json_extract(pipeline_stage_events.data_json, '$.size_bytes')
                    END,
                    source_files.size_bytes
                  ) AS size_bytes,
                  COALESCE(
                    CASE
                      WHEN json_valid(pipeline_stage_events.data_json)
                      THEN json_extract(pipeline_stage_events.data_json, '$.page_count')
                    END,
                    source_files.page_count
                  ) AS page_count,
                  COALESCE(
                    CASE
                      WHEN json_valid(pipeline_stage_events.data_json)
                      THEN json_extract(pipeline_stage_events.data_json, '$.pdf_producer')
                    END,
                    source_files.pdf_producer
                  ) AS pdf_producer,
                  COALESCE(
                    CASE
                      WHEN json_valid(pipeline_stage_events.data_json)
                      THEN json_extract(pipeline_stage_events.data_json, '$.parser')
                    END,
                    pipeline_runs.parser
                  ) AS parser
                FROM pipeline_stage_events
                JOIN source_files
                  ON source_files.id = pipeline_stage_events.source_file_id
                LEFT JOIN pipeline_runs
                  ON pipeline_runs.id = pipeline_stage_events.run_id
                WHERE pipeline_stage_events.status = 'completed'
                  AND pipeline_stage_events.duration_seconds IS NOT NULL
                ORDER BY pipeline_stage_events.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_import_batch(
        self,
        *,
        batch_id: str,
        manifest_path: str,
        profile_name: str,
        parser: str,
        parser_version: str,
    ) -> int:
        with self.session() as conn:
            row = conn.execute(
                """
                INSERT INTO batch_imports (
                  batch_id, manifest_path, profile_name, parser, parser_version
                ) VALUES (?, ?, ?, ?, ?)
                RETURNING id
                """,
                (batch_id, manifest_path, profile_name, parser, parser_version),
            ).fetchone()
            if row is None:
                raise RuntimeError("batch import insert did not return an id")
            return int(row["id"])

    def record_import_decision(
        self,
        *,
        batch_import_id: int,
        source_file_id: int | None,
        source_relpath: str,
        manifest_source_sha256: str,
        local_source_sha256: str,
        markdown_path: str,
        markdown_sha256: str,
        validation_status: ImportValidationStatus | str,
        import_mode: str = "strict",
        override_reason: str = "",
        imported: int = 0,
    ) -> int:
        with self.session() as conn:
            resolved_source_file_id = self._resolve_source_file_id(conn, source_file_id)
            row = conn.execute(
                """
                INSERT INTO batch_import_files (
                  batch_import_id, source_file_id, source_relpath, manifest_source_sha256,
                  local_source_sha256, markdown_path, markdown_sha256, validation_status,
                  import_mode, override_reason, imported
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    batch_import_id,
                    resolved_source_file_id,
                    source_relpath,
                    manifest_source_sha256,
                    local_source_sha256,
                    markdown_path,
                    markdown_sha256,
                    str(validation_status),
                    import_mode,
                    override_reason,
                    imported,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("batch import file insert did not return an id")
            return int(row["id"])

    def list_import_batch_files(self, batch_import_id: int) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM batch_import_files
                WHERE batch_import_id = ?
                ORDER BY id ASC
                """,
                (batch_import_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def _resolve_source_file_id(
        self, conn: sqlite3.Connection, source_file_id: int | None
    ) -> int | None:
        if source_file_id is None:
            return None
        row = conn.execute(
            "SELECT id FROM source_files WHERE id = ?",
            (source_file_id,),
        ).fetchone()
        if row is None:
            raise ValueError("source_file_id")
        return int(row["id"])

    def create_job(
        self,
        kind: str,
        source_file_id: int | None = None,
        profile_name: str | None = None,
    ) -> int:
        with self.session() as conn:
            row = conn.execute(
                """
                INSERT INTO jobs (kind, status, source_file_id, profile_name)
                VALUES (?, 'queued', ?, ?)
                RETURNING id
                """,
                (kind, source_file_id, profile_name),
            ).fetchone()
            if row is None:
                raise RuntimeError("job insert did not return an id")
            return int(row["id"])

    def next_queued_job(self) -> dict[str, Any] | None:
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM jobs
                WHERE status = 'queued'
                ORDER BY id
                LIMIT 1
                """
            ).fetchone()
            return dict(row) if row is not None else None

    def update_job_status(
        self,
        job_id: int,
        status: str,
        progress: float | None = None,
        error_summary: str = "",
    ) -> None:
        with self.session() as conn:
            if status == "running":
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?,
                        started_at = CURRENT_TIMESTAMP,
                        progress = COALESCE(?, progress),
                        error_summary = ?
                    WHERE id = ?
                    """,
                    (status, progress, error_summary, job_id),
                )
            elif status in {"completed", "failed", "canceled"}:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?,
                        finished_at = CURRENT_TIMESTAMP,
                        progress = COALESCE(?, progress),
                        error_summary = ?
                    WHERE id = ?
                    """,
                    (status, progress, error_summary, job_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, progress = COALESCE(?, progress), error_summary = ?
                    WHERE id = ?
                    """,
                    (status, progress, error_summary, job_id),
                )

    def list_jobs(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self.session() as conn:
            if limit is None:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM jobs
                    ORDER BY id DESC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM jobs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    def job_counts(self) -> dict[str, int]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
            ).fetchall()
        counts = {"queued": 0, "running": 0, "failed": 0, "completed": 0}
        for row in rows:
            status = str(row["status"])
            if status in counts:
                counts[status] = int(row["count"])
        return counts

    def add_artifact(
        self,
        source_file_id: int,
        parser: str,
        output_path: str,
        output_sha256: str,
        quality_status: str,
        warnings_json: str,
    ) -> int:
        with self.session() as conn:
            row = conn.execute(
                """
                INSERT INTO artifacts (
                  source_file_id, parser, output_path, output_sha256,
                  quality_status, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    source_file_id,
                    parser,
                    output_path,
                    output_sha256,
                    quality_status,
                    warnings_json,
                ),
            ).fetchone()
            conn.execute(
                """
                UPDATE source_files
                SET state = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (SourceState.CONVERTED.value, source_file_id),
            )
            if row is None:
                raise RuntimeError("artifact insert did not return an id")
            return int(row["id"])

    def update_source_state(self, source_file_id: int, state: SourceState) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE source_files
                SET state = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (state.value, source_file_id),
            )

    def latest_artifact_for_source(self, source_file_id: int) -> dict[str, Any] | None:
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM artifacts
                WHERE source_file_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (source_file_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def upsert_ragflow_document(
        self,
        source_file_id: int,
        dataset_id: str,
        dataset_name: str,
        document_id: str,
        document_name: str,
        upload_status: str,
        parse_status: str,
        chunk_count: int | None = None,
        token_count: int | None = None,
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO ragflow_documents (
                  source_file_id, dataset_id, dataset_name, document_id,
                  document_name, upload_status, parse_status, chunk_count, token_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_file_id) DO UPDATE SET
                  dataset_id = excluded.dataset_id,
                  dataset_name = excluded.dataset_name,
                  document_id = excluded.document_id,
                  document_name = excluded.document_name,
                  upload_status = excluded.upload_status,
                  parse_status = excluded.parse_status,
                  chunk_count = excluded.chunk_count,
                  token_count = excluded.token_count,
                  last_synced_at = CURRENT_TIMESTAMP
                """,
                (
                    source_file_id,
                    dataset_id,
                    dataset_name,
                    document_id,
                    document_name,
                    upload_status,
                    parse_status,
                    chunk_count,
                    token_count,
                ),
            )
            conn.execute(
                """
                UPDATE source_files
                SET state = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (SourceState.UPLOADED.value, source_file_id),
            )

    def clear_ragflow_document(self, source_file_id: int) -> None:
        with self.session() as conn:
            conn.execute(
                "DELETE FROM ragflow_documents WHERE source_file_id = ?",
                (source_file_id,),
            )
            conn.execute(
                """
                UPDATE source_files
                SET state = CASE
                    WHEN EXISTS (
                        SELECT 1 FROM artifacts WHERE source_file_id = source_files.id
                    )
                    THEN ?
                    ELSE state
                END,
                updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (SourceState.CONVERTED.value, source_file_id),
            )
