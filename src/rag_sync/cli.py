from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from rag_sync.config import DEFAULT_PROFILE_PATH, load_profiles
from rag_sync.models import Profile
from rag_sync.scanner import scan_profile

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
    table.add_column("State")
    table.add_column("Path")
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

    for profile in profiles_to_scan:
        for result in scan_profile(profile, existing_hashes={}):
            table.add_row(profile.name, result.state, str(result.source_path))
    console.print(table)


if __name__ == "__main__":
    app()
