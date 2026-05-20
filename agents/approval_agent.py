"""
ApprovalAgent

Presents a summary of the dry-run results to a human operator and
waits for an explicit approve / reject decision before migration begins.

In auto-approve mode (MIGRATION_AUTO_APPROVE=true) the agent approves
automatically – useful for CI/CD pipelines.
"""

from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from agents.base_agent import AgentResult, BaseAgent
from models.data_models import ApprovalDecision, MigrationStage, MigrationState
from utils.report_generator import print_dry_run_table, print_schema_mapping

console = Console()


class ApprovalAgent(BaseAgent):
    """Presents dry-run results and requests human approval to proceed."""

    def __init__(self, auto_approve: bool = False, log_level: str = "INFO") -> None:
        super().__init__("ApprovalAgent", log_level)
        self.auto_approve = auto_approve

    # ------------------------------------------------------------------

    def run(self, state: MigrationState) -> AgentResult:
        self._info("=== Approval Agent started ===")

        if not state.dry_run_result:
            err = "No dry run result found. Cannot request approval."
            state.errors.append(err)
            state.stage = MigrationStage.FAILED
            return AgentResult(success=False, state=state, error=err)

        # Present summary
        self._present_summary(state)

        # Decide
        if self.auto_approve:
            self._info("Auto-approve enabled – proceeding without human input")
            decision = ApprovalDecision(approved=True, approver="system", notes="Auto-approved")
        else:
            decision = self._interactive_approval(state)

        state.approval = decision

        if decision.approved:
            self._info("Migration APPROVED by '%s'", decision.approver)
            state.stage = MigrationStage.MIGRATE
            return AgentResult(success=True, state=state, message="Approved – migration will proceed")
        else:
            self._warn("Migration REJECTED by '%s'. Reason: %s", decision.approver, decision.notes)
            state.stage = MigrationStage.FAILED
            state.errors.append(f"Migration rejected by {decision.approver}: {decision.notes}")
            return AgentResult(success=False, state=state, message="Migration rejected by operator")

    # ------------------------------------------------------------------

    def _present_summary(self, state: MigrationState) -> None:
        console.rule("[bold blue]Migration Pre-Approval Summary[/bold blue]")

        # Schema mapping table
        print_schema_mapping(state)

        # Dry run table
        print_dry_run_table(state.dry_run_result)

        # High-level summary panel
        dr = state.dry_run_result
        summary_lines = [
            f"[bold]Migration ID:[/bold]  {state.migration_id}",
            f"[bold]Mode:[/bold]          {'MOCK (no real changes)' if state.is_mock_mode else 'LIVE'}",
            f"[bold]Total records:[/bold] {dr.total_records}",
            f"[bold]To migrate:[/bold]    {dr.valid_records}",
            f"[bold]To skip:[/bold]       {dr.duplicate_count}",
            f"[bold]Errors found:[/bold]  {dr.invalid_records}",
            f"[bold]Est. duration:[/bold] {dr.estimated_duration_seconds:.0f} seconds",
        ]
        console.print(Panel("\n".join(summary_lines), title="Summary", border_style="yellow"))

    def _interactive_approval(self, state: MigrationState) -> ApprovalDecision:
        dr = state.dry_run_result

        if dr.invalid_records > 0:
            console.print(
                f"\n[red]⚠  {dr.invalid_records} records have validation errors. "
                "They will be skipped during migration.[/red]"
            )

        try:
            approved = Confirm.ask(
                "\n[bold yellow]Do you approve this migration?[/bold yellow]",
                default=False,
            )
            approver = Prompt.ask("Your name / operator ID", default="operator")
            notes = Prompt.ask("Notes (optional)", default="")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[red]Approval cancelled by user.[/red]")
            return ApprovalDecision(approved=False, approver="user", notes="Cancelled via keyboard interrupt")

        return ApprovalDecision(
            approved=approved,
            approver=approver.strip() or "operator",
            notes=notes.strip() or None,
        )
