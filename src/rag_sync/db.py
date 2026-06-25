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
