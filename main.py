"""
Cherwell → ServiceNow Migration Tool

CLI entry point.  All configuration is read from environment variables
or a .env file – never from command-line arguments so credentials are
not exposed in shell history.

Usage examples
--------------
# Run full pipeline (mock mode, interactive approval)
python main.py run

# Run with auto-approval (CI/CD)
MIGRATION_AUTO_APPROVE=true python main.py run

# Dry run only (no approval prompt, no migration)
python main.py dry-run

# Show status of last run
python main.py status
"""

import sys
import click
from rich.console import Console

from config.settings import CherwellConfig, MigrationConfig, ServiceNowConfig
from orchestrator.orchestrator import MigrationOrchestrator

console = Console()


@click.group()
def cli() -> None:
    """Cherwell → ServiceNow migration orchestrator."""


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--resume-id",
    default=None,
    help="Resume a previous migration by its ID (found in the state file).",
)
def run(resume_id: str) -> None:
    """Run the full migration pipeline."""
    cherwell_cfg = CherwellConfig()
    servicenow_cfg = ServiceNowConfig()
    migration_cfg = MigrationConfig()

    orchestrator = MigrationOrchestrator(cherwell_cfg, servicenow_cfg, migration_cfg)
    state = orchestrator.run(resume_id=resume_id)

    sys.exit(0 if state.stage.value == "complete" else 1)


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------

@cli.command("dry-run")
def dry_run_cmd() -> None:
    """Fetch records, map schema, and validate – without migrating."""
    cherwell_cfg = CherwellConfig()
    servicenow_cfg = ServiceNowConfig()
    migration_cfg = MigrationConfig()
    migration_cfg.auto_approve = False  # stop before migration

    # Patch: use a subclass that stops after AWAIT_APPROVAL
    class DryRunOrchestrator(MigrationOrchestrator):
        def run(self, resume_id=None):  # type: ignore[override]
            from models.data_models import MigrationStage
            from utils.report_generator import print_dry_run_table, print_schema_mapping

            state = self._new_state()
            if not self._authenticate():
                console.print("[red]Authentication failed[/red]")
                return state

            state = self._dispatch(state)   # INITIALIZE → FETCH_SCHEMA
            state = self._dispatch(state)   # FETCH_SCHEMA → FETCH_RECORDS
            state = self._dispatch(state)   # FETCH_RECORDS → DRY_RUN
            state = self._dispatch(state)   # DRY_RUN runs dry run
            self._save_state(state)

            console.rule("[bold blue]Dry Run Results[/bold blue]")
            print_schema_mapping(state)
            if state.dry_run_result:
                print_dry_run_table(state.dry_run_result)
            return state

    orchestrator = DryRunOrchestrator(cherwell_cfg, servicenow_cfg, migration_cfg)
    orchestrator.run()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@cli.command()
def status() -> None:
    """Display the status of the most recent migration run."""
    import json
    from pathlib import Path
    from rich.table import Table
    from rich import box

    cfg = MigrationConfig()
    path = Path(cfg.state_file)

    if not path.exists():
        console.print("[yellow]No migration state file found.[/yellow]")
        console.print(f"Expected path: {path}")
        return

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    table = Table(title="Last Migration Status", box=box.ROUNDED)
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")

    display_keys = [
        "migration_id", "stage", "total_records",
        "started_at", "completed_at", "is_mock_mode",
    ]
    for key in display_keys:
        val = data.get(key, "-")
        table.add_row(key, str(val))

    console.print(table)

    if data.get("errors"):
        console.print("\n[red]Errors:[/red]")
        for e in data["errors"]:
            console.print(f"  [red]✗[/red] {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
