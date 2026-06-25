from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rag_sync.models import SourceState

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_name TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_type TEXT NOT NULL,
  extension TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
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
"""


class RagSyncDb:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

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
    ) -> int:
        with self.session() as conn:
            row = conn.execute(
                """
                INSERT INTO source_files (
                  profile_name, source_path, source_type, extension, sha256,
                  size_bytes, mtime, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_name, source_path) DO UPDATE SET
                  source_type = excluded.source_type,
                  extension = excluded.extension,
                  sha256 = excluded.sha256,
                  size_bytes = excluded.size_bytes,
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

    def list_source_files(self) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute("SELECT * FROM source_files ORDER BY source_path").fetchall()
            return [dict(row) for row in rows]

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
