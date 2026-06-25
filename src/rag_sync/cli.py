from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from rag_sync.config import DEFAULT_PROFILE_PATH, load_profiles
from rag_sync.models import Profile
from rag_sync.sync import (
    convert_source_file,
    default_db,
    parse_uploaded_document,
    persist_scan,
    upload_latest_artifact,
)

app = typer.Typer(help="RAG Sync CLI")
console = Console()
error_console = Console(stderr=True)


def _load_profiles_or_exit(config: Path) -> list[Profile]:
    try:
        return load_profiles(config)
    except Exception as exc:
        error_console.print(f"[red]Failed to load profiles:[/] {exc}")
        raise typer.Exit(1) from exc


@app.command()
def profiles(config: Path = DEFAULT_PROFILE_PATH) -> None:
    table = Table(title="RAG Sync Profiles")
    table.add_column("Name")
    table.add_column("Parser")
    table.add_column("Dataset")
    table.add_column("Sources")
    for profile in _load_profiles_or_exit(config):
        table.add_row(
            profile.name,
            profile.parser_mode.value,
            profile.target_dataset,
            "\n".join(str(path) for path in profile.source_paths),
        )
    console.print(table)


@app.command()
def scan(profile_name: str | None = None, config: Path = DEFAULT_PROFILE_PATH) -> None:
    table = Table(title="Scan Results")
    table.add_column("Profile")
    table.add_column("Stored Files", justify="right")
    profiles_by_name = {
        profile.name: profile for profile in _load_profiles_or_exit(config)
    }
    if profile_name is None:
        profiles_to_scan = list(profiles_by_name.values())
    else:
        profile = profiles_by_name.get(profile_name)
        if profile is None:
            error_console.print(f"[red]Unknown profile:[/] {profile_name}")
            raise typer.Exit(1)
        profiles_to_scan = [profile]

    db = default_db()
    for profile in profiles_to_scan:
        ids = persist_scan(db, profile)
        table.add_row(profile.name, str(len(ids)))
    console.print(table)


@app.command()
def convert(
    source_file_id: int,
    parser: str | None = None,
    config: Path = DEFAULT_PROFILE_PATH,
) -> None:
    output_path = convert_source_file(default_db(), source_file_id, parser, config)
    console.print(str(output_path))


@app.command()
def upload(source_file_id: int, config: Path = DEFAULT_PROFILE_PATH) -> None:
    result = asyncio.run(upload_latest_artifact(default_db(), source_file_id, profile_path=config))
    console.print(str(result["document_id"]))


@app.command()
def parse(source_file_id: int) -> None:
    asyncio.run(parse_uploaded_document(default_db(), source_file_id))
    console.print(f"Parsed document for source file {source_file_id}")


if __name__ == "__main__":
    app()
