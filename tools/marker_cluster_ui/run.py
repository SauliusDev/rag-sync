from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.panel import Panel

from rag_sync.marker_batch import BatchRunResult, run_batch

CLI_RUNTIME_ERROR_EXIT_CODE = 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Marker batch conversion and print a compact Rich summary."
    )
    parser.add_argument("--input-dir", type=Path, required=True, help="Directory containing PDFs.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where outputs, manifest, and logs are written.",
    )
    parser.add_argument("--profile", required=True, help="Profile name recorded in the manifest.")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Optional manifest tag. Repeat to add more than one tag.",
    )
    parser.add_argument(
        "--marker-bin",
        default="marker",
        help="Marker executable to invoke. Defaults to 'marker'.",
    )
    return parser.parse_args(argv)


def render_summary(console: Console, result: BatchRunResult) -> None:
    console.print(
        Panel.fit(
            "\n".join(
                [
                    f"batch_id: {result.batch_id}",
                    f"success_count: {result.success_count}",
                    f"failure_count: {result.failure_count}",
                    f"manifest_path: {result.manifest_path}",
                    f"log_path: {result.log_path}",
                ]
            ),
            title="Batch Summary",
        )
    )


def main(argv: Sequence[str] | None = None, *, console: Console | None = None) -> int:
    args = parse_args(argv)
    console = console or Console()
    console.print(
        f"[bold]Marker batch[/bold] input={args.input_dir} output={args.output_dir} profile={args.profile}"
    )
    try:
        result = run_batch(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            profile=args.profile,
            tags=tuple(args.tag),
            marker_bin=args.marker_bin,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"[bold red]Marker batch failed:[/bold red] {exc}")
        return CLI_RUNTIME_ERROR_EXIT_CODE
    render_summary(console, result)
    return 0 if result.failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
