"""
Report generation utilities.
Produces JSON summary reports and a human-readable Rich table to the console.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from models.data_models import MigrationState, DryRunResult, MigrationResult

console = Console()


def ensure_output_dir(output_dir: str) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_json_report(state: MigrationState, output_dir: str) -> str:
    """Persist the full migration state as a JSON report and return the path."""
    out = ensure_output_dir(output_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = out / f"migration_report_{ts}.json"
    with open(filename, "w", encoding="utf-8") as fh:
        fh.write(state.model_dump_json(indent=2))
    return str(filename)


def print_dry_run_table(result: DryRunResult) -> None:
    """Render a summary of the dry-run results to the console."""
    table = Table(title="Dry Run Summary", box=box.ROUNDED, border_style="cyan")
    table.add_column("Metric", style="bold white")
    table.add_column("Value", style="cyan")

    table.add_row("Total records fetched", str(result.total_records))
    table.add_row("Valid records", f"[green]{result.valid_records}[/green]")
    table.add_row("Invalid records", f"[red]{result.invalid_records}[/red]")
    table.add_row("Potential duplicates", str(result.duplicate_count))
    table.add_row("Estimated duration (s)", f"{result.estimated_duration_seconds:.1f}")

    console.print(table)

    if result.warnings:
        console.print("\n[yellow]Warnings:[/yellow]")
        for w in result.warnings:
            console.print(f"  [yellow]⚠[/yellow]  {w}")

    if result.errors:
        console.print("\n[red]Errors:[/red]")
        for e in result.errors:
            console.print(f"  [red]✗[/red]  {e}")


def print_migration_result(result: MigrationResult) -> None:
    """Render the post-migration result table to the console."""
    table = Table(title="Migration Result", box=box.ROUNDED, border_style="green")
    table.add_column("Metric", style="bold white")
    table.add_column("Value", style="green")

    table.add_row("Total records", str(result.total_records))
    table.add_row("Successful", f"[green]{result.successful}[/green]")
    table.add_row("Failed", f"[red]{result.failed}[/red]")
    table.add_row("Skipped", f"[yellow]{result.skipped}[/yellow]")
    table.add_row("Duration (s)", f"{result.duration_seconds:.1f}")
    table.add_row("Batches processed", str(result.batches_processed))

    console.print(table)

    if result.failed_record_ids:
        console.print("\n[red]Failed record IDs:[/red]")
        for rid in result.failed_record_ids:
            console.print(f"  {rid}")


def print_schema_mapping(state: MigrationState) -> None:
    """Render the generated field mappings to the console."""
    if not state.schema_mapping:
        return
    table = Table(
        title="Schema Field Mapping",
        box=box.SIMPLE_HEAD,
        border_style="blue",
        show_lines=True,
    )
    table.add_column("Cherwell Field", style="bold cyan")
    table.add_column("ServiceNow Field", style="bold green")
    table.add_column("Transform", style="yellow")
    table.add_column("Required", style="white")

    for fm in state.schema_mapping.field_mappings:
        table.add_row(
            fm.source_field,
            fm.target_field,
            fm.transform or "-",
            "✓" if fm.required else "",
        )
    console.print(table)


def print_banner(title: str, subtitle: str = "") -> None:
    console.print(
        Panel(
            f"[bold white]{title}[/bold white]\n[dim]{subtitle}[/dim]",
            border_style="blue",
            expand=False,
        )
    )
