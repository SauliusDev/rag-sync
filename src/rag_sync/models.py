from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class ParserMode(StrEnum):
    MARKER = "marker"
    MINERU = "mineru"
    PASSTHROUGH = "passthrough"


class SourceState(StrEnum):
    NEW = "new"
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    EXCLUDED = "excluded"
    CONVERTED = "converted"
    UPLOADED = "uploaded"
    PARSED = "parsed"
    FAILED = "failed"
    MISSING = "missing"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class JobKind(StrEnum):
    SCAN = "scan"
    CONVERT = "convert"
    UPLOAD = "upload"
    PARSE = "parse"
    STOP_RAGFLOW = "stop_ragflow"
    DELETE_RAGFLOW = "delete_ragflow"
    RESTART_RAGFLOW = "restart_ragflow"
    SYNC_FILE = "sync_file"
    SYNC_FILTERED = "sync_filtered"
    RETRIEVAL_TEST = "retrieval_test"


@dataclass(frozen=True)
class SkipRules:
    path_parts: tuple[str, ...] = field(default_factory=tuple)
    suffixes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Profile:
    name: str
    source_paths: tuple[Path, ...]
    file_types: tuple[str, ...]
    parser_mode: ParserMode
    target_dataset: str
    source_type: str
    enabled: bool = True
    output_dir: Path | None = None
    skip_rules: SkipRules = field(default_factory=SkipRules)
    max_convert_workers: int = 1
    max_upload_workers: int = 4
    max_parse_workers: int = 2


@dataclass(frozen=True)
class DiscoveredFile:
    profile_name: str
    source_path: Path
    source_type: str
    extension: str
    sha256: str
    size_bytes: int
    mtime: float
