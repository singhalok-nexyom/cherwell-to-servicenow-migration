"""
LLMReviewAgent

Uses an LLM to analyse migration logs and dry-run results, then presents
an AI-generated risk assessment and recommendation to a human operator
(Human-in-the-Loop review gate).

The operator may:
  - **Approve** → pipeline advances to AWAIT_APPROVAL then MIGRATE.
  - **Reject**  → pipeline resets to INITIALIZE so the process can be
                  restarted (e.g. after adjusting configuration or data).

LLM backend
-----------
When ``mock_mode=True`` (default) no external API call is made and a
locally-generated analysis is returned – ideal for CI and testing.

When ``mock_mode=False`` the agent calls an OpenAI-compatible REST
endpoint (configurable via LLM_BASE_URL / LLM_API_KEY / LLM_MODEL env
vars).  The ``requests`` library (already a project dependency) is used
directly so no additional package is required.
"""

from datetime import datetime
from typing import Any, Dict, List

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from agents.base_agent import AgentResult, BaseAgent
from models.data_models import (
    LLMReviewDecision,
    MigrationStage,
    MigrationState,
)

console = Console()

# ---------------------------------------------------------------------------
# Prompt template sent to the LLM
# ---------------------------------------------------------------------------

_ANALYSIS_PROMPT = """\
You are a data migration risk analyst. Review the following Cherwell to ServiceNow
migration plan and provide a concise markdown report covering:

1. **Risk Assessment** (Low / Medium / High) with reasoning
2. **Data Quality Summary** – key observations from the dry-run results
3. **Schema Mapping Analysis** – potential field-mapping issues or conflicts
4. **Recommendations** – actions the operator should consider before approving
5. **Go / No-Go Verdict** – your final recommendation

## Migration Overview
- Migration ID   : {migration_id}
- Mode           : {mode}
- Total Records  : {total_records}
- Valid Records  : {valid_records}
- Invalid Records: {invalid_records}
- Duplicates     : {duplicate_count}
- Est. Duration  : {estimated_duration:.0f} seconds
- Pipeline Errors: {error_count}
- Warnings       : {warning_count}

## Schema Field Mappings
{field_mappings}

## Dry-Run Errors
{dry_run_errors}

## Dry-Run Warnings
{dry_run_warnings}

## Recent Log Events
{log_events}

Provide your analysis in clear markdown. Be concise and direct.
"""

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

_MAX_LOG_LINES = 10  # cap log events sent to LLM
_NONE_PLACEHOLDER = "(none)"


class LLMReviewAgent(BaseAgent):
    """LLM-powered pre-migration analysis with Human-in-the-Loop approval gate.

    If the operator rejects the migration the agent sets the pipeline stage
    back to INITIALIZE, signalling the orchestrator to restart from scratch.
    """

    def __init__(
        self,
        mock_mode: bool = True,
        llm_api_key: str = "",
        llm_base_url: str = "https://api.openai.com/v1",
        llm_model: str = "gpt-4o-mini",
        llm_timeout: int = 60,
        auto_approve: bool = False,
        log_level: str = "INFO",
    ) -> None:
        super().__init__("LLMReviewAgent", log_level)
        self.mock_mode = mock_mode or not llm_api_key
        self.llm_api_key = llm_api_key
        self.llm_base_url = llm_base_url.rstrip("/")
        self.llm_model = llm_model
        self.llm_timeout = llm_timeout
        self.auto_approve = auto_approve

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, state: MigrationState) -> AgentResult:
        self._info("=== LLM Review Agent started ===")

        if not state.dry_run_result:
            err = "No dry-run result available – run DryRunAgent first."
            state.errors.append(err)
            state.stage = MigrationStage.FAILED
            return AgentResult(success=False, state=state, error=err)

        # 1. Build context dict from current pipeline state
        ctx = self._build_context(state)

        # 2. Generate AI analysis
        console.rule("[bold magenta]AI Migration Review[/bold magenta]")
        console.print("[dim]Generating AI analysis of migration plan…[/dim]\n")
        analysis = self._generate_analysis(ctx)

        # 3. Display analysis to the operator
        self._display_analysis(analysis, state)

        # 4. HIL gate – approve or reject
        if self.auto_approve:
            self._info("Auto-approve mode active – skipping interactive prompt")
            decision = LLMReviewDecision(
                approved=True,
                reviewer="system",
                notes="Auto-approved (CI mode)",
                llm_analysis=analysis,
                attempt=state.restart_count + 1,
            )
        else:
            decision = self._interactive_review(analysis, state)

        state.llm_review = decision

        if decision.approved:
            self._info("Migration APPROVED by '%s' after LLM review", decision.reviewer)
            state.stage = MigrationStage.AWAIT_APPROVAL
            return AgentResult(
                success=True,
                state=state,
                message=f"LLM review approved by {decision.reviewer}",
            )

        # Rejection → signal orchestrator to restart from scratch
        self._warn(
            "Migration REJECTED by '%s'. Reason: %s – pipeline will restart",
            decision.reviewer,
            decision.notes,
        )
        state.stage = MigrationStage.INITIALIZE   # orchestrator detects and creates fresh state
        state.errors.append(
            f"LLM review rejected by {decision.reviewer}: {decision.notes}"
        )
        return AgentResult(
            success=False,
            state=state,
            message="Migration rejected – pipeline restart requested",
            error="LLMReviewRejected",
        )

    # ------------------------------------------------------------------
    # Context builder
    # ------------------------------------------------------------------

    def _build_context(self, state: MigrationState) -> Dict[str, Any]:
        dr = state.dry_run_result

        fm_lines: List[str] = []
        if state.schema_mapping:
            for fm in state.schema_mapping.field_mappings:
                req_tag = " [REQUIRED]" if fm.required else ""
                xform = f"  (transform: {fm.transform})" if fm.transform else ""
                fm_lines.append(f"- {fm.source_field} → {fm.target_field}{xform}{req_tag}")
        field_mappings_txt = "\n".join(fm_lines) if fm_lines else _NONE_PLACEHOLDER

        return {
            "migration_id": state.migration_id,
            "mode": "MOCK" if state.is_mock_mode else "LIVE",
            "total_records": dr.total_records,
            "valid_records": dr.valid_records,
            "invalid_records": dr.invalid_records,
            "duplicate_count": dr.duplicate_count,
            "estimated_duration": dr.estimated_duration_seconds,
            "error_count": len(state.errors),
            "warning_count": len(state.warnings),
            "field_mappings": field_mappings_txt,
            "dry_run_errors": "\n".join(f"- {e}" for e in dr.errors) or _NONE_PLACEHOLDER,
            "dry_run_warnings": "\n".join(f"- {w}" for w in dr.warnings) or _NONE_PLACEHOLDER,
            "log_events": self._collect_log_events(state),
        }

    def _collect_log_events(self, state: MigrationState) -> str:
        lines: List[str] = [
            f"[{state.started_at}] Pipeline started – id={state.migration_id}",
        ]
        if state.restart_count:
            lines.append(f"Pipeline restarted {state.restart_count} time(s)")
        if state.schema_mapping:
            lines.append(
                f"Schema mapping loaded: {len(state.schema_mapping.field_mappings)} fields"
            )
        dr = state.dry_run_result
        if dr:
            lines.append(
                f"Dry run: {dr.total_records} total / {dr.valid_records} valid / "
                f"{dr.invalid_records} invalid / {dr.duplicate_count} duplicates"
            )
        for err in state.errors[-_MAX_LOG_LINES:]:
            lines.append(f"ERROR: {err}")
        for warn in state.warnings[-_MAX_LOG_LINES:]:
            lines.append(f"WARN:  {warn}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # LLM call (mock or live)
    # ------------------------------------------------------------------

    def _generate_analysis(self, ctx: Dict[str, Any]) -> str:
        if self.mock_mode:
            return self._mock_analysis(ctx)
        return self._call_llm_api(ctx)

    # ------------------------------------------------------------------
    # Mock analysis (no external call, deterministic)
    # ------------------------------------------------------------------

    def _mock_analysis(self, ctx: Dict[str, Any]) -> str:
        invalid = ctx["invalid_records"]
        total = ctx["total_records"]
        mode = ctx["mode"]
        error_count = ctx["error_count"]

        if invalid == 0 and error_count == 0:
            risk, verdict = "Low", "✅ **GO** – Proceed with migration"
        elif invalid > total * 0.1 or error_count > 2:
            risk = "High"
            verdict = "❌ **NO-GO** – Significant data quality issues detected"
        else:
            risk = "Medium"
            verdict = "⚠️  **CONDITIONAL GO** – Resolve warnings before proceeding"

        quality_note = (
            "No data quality issues detected."
            if invalid == 0
            else f"⚠️  {invalid} record(s) will be **skipped** due to validation failures."
        )
        required_note = (
            "All required fields are mapped correctly."
            if "[REQUIRED]" not in ctx["field_mappings"]
            else "Required field mappings are present – verify transforms produce valid ServiceNow values."
        )
        live_warning = (
            "- 🔴 Running in **LIVE mode** – real data will be modified. Ensure backups exist."
            if mode == "LIVE"
            else "- ℹ️  Running in **MOCK mode** – no real data will be changed."
        )

        return (
            f"## AI Migration Risk Analysis\n\n"
            f"**Migration ID:** `{ctx['migration_id']}`  \n"
            f"**Mode:** {mode}  \n"
            f"**Analysis generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "---\n\n"
            f"### 1. Risk Assessment: **{risk}**\n\n"
            f"{'Records appear well-formed with no significant data quality issues.' if risk == 'Low' else f'Found **{invalid}** invalid record(s) and **{error_count}** pipeline error(s).'}\n\n"
            "---\n\n"
            "### 2. Data Quality Summary\n\n"
            "| Metric | Value |\n"
            "|--------|-------|\n"
            f"| Total Records | {ctx['total_records']} |\n"
            f"| Valid | {ctx['valid_records']} |\n"
            f"| Invalid | {ctx['invalid_records']} |\n"
            f"| Duplicates | {ctx['duplicate_count']} |\n"
            f"| Est. Duration | {ctx['estimated_duration']:.0f}s |\n\n"
            f"{quality_note}\n\n"
            "---\n\n"
            "### 3. Schema Mapping Analysis\n\n"
            f"```\n{ctx['field_mappings']}\n```\n\n"
            f"{required_note}\n\n"
            "---\n\n"
            "### 4. Recommendations\n\n"
            f"{'- ✅ No pre-migration actions required.' if risk == 'Low' else ''}"
            f"{'- ⚠️  Review and resolve invalid records before migration.' if invalid > 0 else ''}\n"
            f"{'- ⚠️  Investigate pipeline errors listed above.' if error_count > 0 else ''}\n"
            f"{live_warning}\n"
            f"- ℹ️  Estimated duration: {ctx['estimated_duration']:.0f} seconds.\n\n"
            "---\n\n"
            f"### 5. Verdict\n\n{verdict}\n"
        )

    # ------------------------------------------------------------------
    # Live LLM API call (OpenAI-compatible)
    # ------------------------------------------------------------------

    def _call_llm_api(self, ctx: Dict[str, Any]) -> str:
        try:
            import requests  # noqa: PLC0415 – intentional lazy import

            prompt = _ANALYSIS_PROMPT.format(**ctx)
            headers = {
                "Authorization": f"Bearer {self.llm_api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.llm_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert data migration analyst specialising in "
                            "Cherwell to ServiceNow migrations. Be concise and precise."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 1200,
            }
            response = requests.post(
                f"{self.llm_base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.llm_timeout,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

        except Exception as exc:  # noqa: BLE001
            self._warn("LLM API call failed (%s) – falling back to mock analysis", exc)
            return self._mock_analysis(ctx)

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def _display_analysis(self, analysis: str, state: MigrationState) -> None:
        dr = state.dry_run_result
        meta = "\n".join([
            f"[bold]Migration ID:[/bold]  {state.migration_id}",
            f"[bold]Mode:[/bold]          {'MOCK' if state.is_mock_mode else 'LIVE'}",
            f"[bold]Records:[/bold]       "
            f"{dr.total_records} total / {dr.valid_records} valid / "
            f"{dr.invalid_records} invalid",
            f"[bold]Restart attempt:[/bold] {state.restart_count + 1}",
        ])
        console.print(
            Panel(meta, title="[bold blue]Migration Overview[/bold blue]", border_style="blue")
        )
        console.print(Markdown(analysis))

    # ------------------------------------------------------------------
    # Interactive HIL gate
    # ------------------------------------------------------------------

    def _interactive_review(
        self, analysis: str, state: MigrationState
    ) -> LLMReviewDecision:
        console.rule("[bold yellow]Human Review Required[/bold yellow]")
        console.print(
            "\n[bold]Please review the AI analysis above and decide whether "
            "to proceed with the migration.[/bold]\n"
        )

        dr = state.dry_run_result
        if dr and dr.invalid_records > 0:
            console.print(
                f"[red]⚠  {dr.invalid_records} record(s) will be skipped "
                "(validation errors).[/red]\n"
            )

        if state.restart_count > 0:
            console.print(
                f"[yellow]ℹ  This is restart attempt #{state.restart_count + 1}.[/yellow]\n"
            )

        try:
            approved = Confirm.ask(
                "[bold yellow]Approve this migration?[/bold yellow]",
                default=False,
            )
            reviewer = Prompt.ask("Your name / operator ID", default="operator")
            notes = ""
            if not approved:
                notes = Prompt.ask(
                    "Reason for rejection (optional)",
                    default="Rejected by operator",
                )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[red]Review interrupted – rejecting migration.[/red]")
            approved = False
            reviewer = "system"
            notes = "Review interrupted"

        return LLMReviewDecision(
            approved=approved,
            reviewer=reviewer,
            notes=notes,
            llm_analysis=analysis,
            attempt=state.restart_count + 1,
        )
