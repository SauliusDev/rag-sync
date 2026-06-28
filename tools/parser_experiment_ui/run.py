from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from rag_sync.parser_experiments import (
    default_variant_specs,
    parser_env_summary,
    run_variant,
    write_candidate_result,
    write_experiment_manifest,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run parser variant experiments with visual-audit prep.")
    parser.add_argument("--source-pdf", action="append", required=True, help="PDF to experiment on. Repeatable.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Experiment output root.")
    parser.add_argument("--source-type", default="book", help="Source type for quality heuristics.")
    parser.add_argument("--sample-pages", type=int, default=7, help="Prepared visual-audit pages per candidate.")
    parser.add_argument("--audit-seed", type=int, default=0, help="Seed for visual-audit page sampling.")
    parser.add_argument("--target-score", type=float, default=0.9, help="Desired agent audit score.")
    return parser.parse_args(argv)


def _render_source_summary(console: Console, args: argparse.Namespace) -> None:
    summary = parser_env_summary()
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("Sources", str(len(args.source_pdf)))
    table.add_row("Output", str(args.output_dir))
    table.add_row("Sample pages", str(args.sample_pages))
    table.add_row("Target score", f"{args.target_score:.2f}")
    table.add_row("MinerU", "available" if summary["mineru_available"] else "unavailable")
    table.add_row("Marker timeout", str(summary["marker_timeout_seconds"]))
    table.add_row("MinerU timeout", str(summary["mineru_timeout_seconds"]))
    console.print(Panel(table, title="Experiment Setup", border_style="blue"))


def _render_result_table(results: list[dict[str, object]]) -> Table:
    table = Table(title="Variant Results")
    table.add_column("Source")
    table.add_column("Variant")
    table.add_column("Parser")
    table.add_column("State")
    table.add_column("Bytes", justify="right")
    table.add_column("Quality")
    table.add_column("Duration", justify="right")
    for row in results:
        table.add_row(
            str(row["source"]),
            str(row["variant"]),
            str(row["parser"]),
            str(row["state"]),
            str(row["bytes"]),
            str(row["quality"]),
            str(row["duration"]),
        )
    return table


def main(argv: list[str] | None = None, *, console: Console | None = None) -> int:
    args = parse_args(argv)
    console = console or Console()
    source_paths = [Path(item).resolve() for item in args.source_pdf]
    variants = default_variant_specs()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    _render_source_summary(console, args)

    total = len(source_paths) * len(variants)
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Experimenting[/bold blue]"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TextColumn("{task.completed}/{task.total} variants"),
    )
    task_id = progress.add_task("experiment", total=total)
    rows: list[dict[str, object]] = []

    with progress:
        for source_pdf in source_paths:
            source_dir = args.output_dir / source_pdf.stem
            source_dir.mkdir(parents=True, exist_ok=True)
            candidate_results = []
            for variant in variants:
                result = run_variant(
                    source_pdf=source_pdf,
                    source_type=args.source_type,
                    output_root=source_dir,
                    variant=variant,
                    sample_pages=args.sample_pages,
                    audit_seed=args.audit_seed,
                    target_score=args.target_score,
                )
                candidate_results.append(result)
                write_candidate_result(
                    result,
                    output_path=source_dir / variant.label / "candidate-result.json",
                )
                rows.append(
                    {
                        "source": source_pdf.name,
                        "variant": result.label,
                        "parser": result.parser,
                        "state": result.state,
                        "bytes": result.markdown_size_bytes,
                        "quality": result.quality_status,
                        "duration": f"{result.duration_seconds:.1f}s",
                    }
                )
                progress.update(task_id, advance=1)
            write_experiment_manifest(
                source_pdf=source_pdf,
                output_dir=source_dir,
                target_score=args.target_score,
                variants=variants,
                results=candidate_results,
            )

    console.print(_render_result_table(rows))
    console.print(
        Panel(
            "Each candidate now has:\n"
            "- `candidate-result.json`\n"
            "- rendered visual-audit bundle under `visual-audit/`\n"
            "- `agent-audit.json` score template to be filled by agent review",
            title="Next Step",
            border_style="green",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
