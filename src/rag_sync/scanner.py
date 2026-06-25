from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from rag_sync.models import DiscoveredFile, Profile, SourceState


@dataclass(frozen=True)
class ScanResult(DiscoveredFile):
    state: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_skipped(path: Path, profile: Profile) -> bool:
    path_parts = set(path.parts)
    if any(part in path_parts for part in profile.skip_rules.path_parts):
        return True

    name = path.name.lower()
    return any(name.endswith(suffix.lower()) for suffix in profile.skip_rules.suffixes)


def discover_files(profile: Profile) -> Iterable[DiscoveredFile]:
    allowed_extensions = {item.lower().lstrip(".") for item in profile.file_types}
    for source_root in profile.source_paths:
        if not source_root.exists():
            continue

        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or _is_skipped(path, profile):
                continue

            extension = path.suffix.lower().lstrip(".")
            if extension not in allowed_extensions:
                continue

            stat = path.stat()
            yield DiscoveredFile(
                profile_name=profile.name,
                source_path=path,
                source_type=profile.source_type,
                extension=extension,
                sha256=sha256_file(path),
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
            )


def scan_profile(profile: Profile, existing_hashes: dict[str, str]) -> list[ScanResult]:
    results: list[ScanResult] = []
    for item in discover_files(profile):
        previous = existing_hashes.get(str(item.source_path))
        state = SourceState.NEW.value if previous is None else SourceState.UNCHANGED.value
        if previous is not None and previous != item.sha256:
            state = SourceState.CHANGED.value
        results.append(ScanResult(**item.__dict__, state=state))
    return results
