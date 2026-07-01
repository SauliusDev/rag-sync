from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from src.config import DEFAULT_PROFILE_PATH, load_profiles
from src.marker_batch import run_batch as run_marker_batch
from src.models import Profile
from src.sync import (
    convert_source_file,
    default_db,
    parse_uploaded_document,
    persist_scan,
    upload_latest_artifact,
)
from src.visual_audit import (
    append_significant_finding_to_mind,
    audit_manifest,
    auditor_from_env,
    prepare_manifest_visual_audit,
    write_batch_summary,
    write_book_audit_report,
    write_prepared_manifest_summary,
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


@app.command("marker-batch-run")
def marker_batch_run(
    input_dir: Annotated[Path, typer.Option("--input-dir")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    profile: Annotated[str, typer.Option("--profile")],
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    marker_bin: Annotated[str, typer.Option("--marker-bin")] = "marker",
) -> None:
    try:
        result = run_marker_batch(
            input_dir=input_dir,
            output_dir=output_dir,
            profile=profile,
            tags=tuple(tag or ()),
            marker_bin=marker_bin,
        )
    except Exception as exc:
        error_console.print(f"[red]Marker batch run failed:[/] {exc}")
        raise typer.Exit(1) from exc
    typer.echo(
        json.dumps(
            {
                "batch_id": result.batch_id,
                "success_count": result.success_count,
                "failure_count": result.failure_count,
                "manifest_path": result.manifest_path,
                "log_path": result.log_path,
            },
            indent=2,
            default=str,
        )
    )


@app.command("visual-audit-manifest")
def visual_audit_manifest(
    manifest_path: Annotated[Path, typer.Option("--manifest-path")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    sample_pages: Annotated[int, typer.Option("--sample-pages")] = 7,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    model: Annotated[str, typer.Option("--model")] = "gpt-5.4",
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds")] = 180,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    only_file: Annotated[list[str] | None, typer.Option("--only-file")] = None,
    mind_path: Annotated[Path | None, typer.Option("--mind-path")] = None,
    update_mind: Annotated[bool, typer.Option("--update-mind/--no-update-mind")] = True,
) -> None:
    if sample_pages < 1:
        error_console.print("[red]sample-pages must be at least 1[/]")
        raise typer.Exit(1)
    try:
        auditor = auditor_from_env(model=model, timeout_seconds=timeout_seconds)
        audits = audit_manifest(
            manifest_path=manifest_path,
            output_dir=output_dir,
            sample_count=sample_pages,
            seed=seed,
            auditor=auditor,
            limit=limit,
            only_files=set(only_file or ()),
        )
    except Exception as exc:
        error_console.print(f"[red]Visual audit failed:[/] {exc}")
        raise typer.Exit(1) from exc
    if not audits:
        error_console.print("[red]Visual audit did not find any eligible files[/]")
        raise typer.Exit(1)

    summary_path = write_batch_summary(audits, output_path=output_dir / "summary.md")
    for audit in audits:
        report_path = output_dir / f"{audit.source_pdf.stem}.json"
        write_book_audit_report(audit, output_path=report_path)
        if update_mind and mind_path is not None:
            append_significant_finding_to_mind(
                mind_path=mind_path,
                source_pdf=audit.source_pdf,
                verdict=audit.verdict,
                reasons=audit.reasons,
                settings_label=f"visual audit model={model} sample_pages={sample_pages}",
            )

    table = Table(title="Visual Audit Results")
    table.add_column("Book")
    table.add_column("Verdict")
    table.add_column("Text", justify="right")
    table.add_column("Formula", justify="right")
    table.add_column("Coverage", justify="right")
    for audit in audits:
        table.add_row(
            audit.source_pdf.stem,
            audit.verdict,
            f"{audit.average_text_fidelity:.3f}",
            f"{audit.average_formula_fidelity:.3f}",
            f"{audit.average_coverage:.3f}",
        )
    console.print(table)
    console.print(str(summary_path))


@app.command("visual-audit-prepare")
def visual_audit_prepare(
    manifest_path: Annotated[Path, typer.Option("--manifest-path")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    sample_pages: Annotated[int, typer.Option("--sample-pages")] = 7,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    only_file: Annotated[list[str] | None, typer.Option("--only-file")] = None,
    dpi: Annotated[int, typer.Option("--dpi")] = 170,
) -> None:
    if sample_pages < 1:
        error_console.print("[red]sample-pages must be at least 1[/]")
        raise typer.Exit(1)
    try:
        prepared = prepare_manifest_visual_audit(
            manifest_path=manifest_path,
            output_dir=output_dir,
            sample_count=sample_pages,
            seed=seed,
            limit=limit,
            only_files=set(only_file or ()),
            dpi=dpi,
        )
    except Exception as exc:
        error_console.print(f"[red]Visual audit preparation failed:[/] {exc}")
        raise typer.Exit(1) from exc
    if not prepared:
        error_console.print("[red]Visual audit preparation did not find any eligible files[/]")
        raise typer.Exit(1)
    summary_path = write_prepared_manifest_summary(
        prepared,
        output_path=output_dir / "prep-summary.md",
    )
    table = Table(title="Visual Audit Prep")
    table.add_column("Book")
    table.add_column("Pages", justify="right")
    table.add_column("Sampled")
    for bundle in prepared:
        table.add_row(
            bundle.source_pdf.stem,
            str(bundle.page_count),
            ", ".join(str(page) for page in bundle.sampled_pages),
        )
    console.print(table)
    console.print(str(summary_path))


if __name__ == "__main__":
    app()
