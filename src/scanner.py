from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from src.models import DiscoveredFile, Profile, SourceState


@dataclass(frozen=True)
class ScanResult(DiscoveredFile):
    state: str = ""


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


def pdf_metadata(path: Path) -> dict[str, object]:
    try:
        proc = subprocess.run(
            ["pdfinfo", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return {"page_count": None, "pdf_producer": ""}
    if proc.returncode != 0:
        return {"page_count": None, "pdf_producer": ""}

    page_count: int | None = None
    producer = ""
    for line in proc.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower()
        normalized_value = value.strip()
        if normalized_key == "pages":
            try:
                page_count = int(normalized_value)
            except ValueError:
                page_count = None
        elif normalized_key == "producer":
            producer = normalized_value
    return {"page_count": page_count, "pdf_producer": producer}


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
            metadata = {"page_count": None, "pdf_producer": ""}
            if extension == "pdf":
                metadata = pdf_metadata(path)
            yield DiscoveredFile(
                profile_name=profile.name,
                source_path=path,
                source_type=profile.source_type,
                extension=extension,
                sha256=sha256_file(path),
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
                page_count=metadata["page_count"],
                pdf_producer=str(metadata["pdf_producer"]),
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


def backfill_pdf_metadata(db: object, profile_names: set[str] | None = None) -> int:
    rows = getattr(db, "list_source_files")(profile_names=profile_names)
    updated = 0
    for row in rows:
        if str(row.get("extension", "")).lower() != "pdf":
            continue
        if row.get("page_count") is not None and str(row.get("pdf_producer", "")).strip():
            continue
        metadata = pdf_metadata(Path(str(row["source_path"])))
        getattr(db, "update_source_pdf_metadata")(
            int(row["id"]),
            page_count=metadata["page_count"],
            pdf_producer=str(metadata["pdf_producer"]),
        )
        updated += 1
    return updated
