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


class JobKind(StrEnum):
    SCAN = "scan"
    CONVERT = "convert"
    UPLOAD = "upload"
    PARSE = "parse"
    RETRIEVAL_TEST = "retrieval_test"


@dataclass(frozen=True)
class SkipRules:
    path_parts: list[str] = field(default_factory=list)
    suffixes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Profile:
    name: str
    source_paths: list[Path]
    file_types: list[str]
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
