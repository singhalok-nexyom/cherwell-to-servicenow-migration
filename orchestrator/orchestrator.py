"""
MigrationOrchestrator

Central state-machine that drives the entire migration pipeline.
It wires together all agents, advances the pipeline stage-by-stage,
persists state to disk after each stage (resumability), and surfaces
a final report.

Pipeline stages
---------------
INITIALIZE → FETCH_SCHEMA → MAP_SCHEMA → FETCH_RECORDS →
DRY_RUN → AWAIT_APPROVAL → MIGRATE → VALIDATE → COMPLETE
                                      ↘ FAILED (any stage)
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from rich.console import Console
from rich.rule import Rule

from agents.approval_agent import ApprovalAgent
from agents.dry_run_agent import DryRunAgent
from agents.migration_agent import MigrationAgent
from agents.schema_mapper_agent import SchemaMappingAgent
from agents.validation_agent import ValidationAgent
from config.settings import CherwellConfig, MigrationConfig, ServiceNowConfig
from connectors.cherwell_connector import CherwellConnector
from connectors.servicenow_connector import ServiceNowConnector
from models.data_models import (
    CherwellRecord,
    MigrationRecord,
    MigrationStage,
    MigrationState,
)
from utils.logger import get_logger
from utils.report_generator import (
    print_banner,
    print_migration_result,
    save_json_report,
)

console = Console()
logger = get_logger(__name__)


class MigrationOrchestrator:
    """Manages the full Cherwell → ServiceNow migration pipeline."""

    def __init__(
        self,
        cherwell_cfg: Optional[CherwellConfig] = None,
        servicenow_cfg: Optional[ServiceNowConfig] = None,
        migration_cfg: Optional[MigrationConfig] = None,
    ) -> None:
        self.cherwell_cfg = cherwell_cfg or CherwellConfig()
        self.servicenow_cfg = servicenow_cfg or ServiceNowConfig()
        self.migration_cfg = migration_cfg or MigrationConfig()

        mock = self.migration_cfg.mock_mode

        # Connectors
        self.cherwell = CherwellConnector(self.cherwell_cfg, mock_mode=mock)
        self.servicenow = ServiceNowConnector(self.servicenow_cfg, mock_mode=mock)

        # Agents
        self.schema_agent = SchemaMappingAgent(
            self.cherwell,
            self.servicenow,
            self.migration_cfg.log_level,
        )
        self.dry_run_agent = DryRunAgent(self.servicenow, self.migration_cfg.log_level)
        self.approval_agent = ApprovalAgent(
            auto_approve=self.migration_cfg.auto_approve,
            log_level=self.migration_cfg.log_level,
        )
        self.migration_agent = MigrationAgent(
            self.servicenow,
            batch_size=self.migration_cfg.batch_size,
            max_retries=self.migration_cfg.max_retries,
            log_level=self.migration_cfg.log_level,
        )
        self.validation_agent = ValidationAgent(
            self.servicenow, self.migration_cfg.log_level
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, resume_id: Optional[str] = None) -> MigrationState:
        """Execute (or resume) the migration pipeline and return the final state."""
        print_banner(
            "Cherwell → ServiceNow Migration",
            f"Mock mode: {self.migration_cfg.mock_mode}  |  "
            f"Batch size: {self.migration_cfg.batch_size}",
        )

        # Load or create state
        state = self._load_state(resume_id) if resume_id else self._new_state()

        logger.info("Migration ID: %s  |  Starting stage: %s", state.migration_id, state.stage)

        # Authenticate connectors
        if not self._authenticate():
            state.stage = MigrationStage.FAILED
            state.errors.append("Authentication failed for one or more connectors")
            self._save_state(state)
            return state

        # ---- Stage machine loop ----
        while state.stage not in (MigrationStage.COMPLETE, MigrationStage.FAILED):
            console.print(Rule(f"[bold cyan]Stage: {state.stage.value.upper()}[/bold cyan]"))
            state = self._dispatch(state)
            self._save_state(state)

        # Final report
        self._finalise(state)
        return state

    # ------------------------------------------------------------------
    # Stage dispatcher
    # ------------------------------------------------------------------

    def _dispatch(self, state: MigrationState) -> MigrationState:
        stage = state.stage
        try:
            if stage == MigrationStage.INITIALIZE:
                state = self._stage_initialize(state)

            elif stage == MigrationStage.FETCH_SCHEMA:
                result = self.schema_agent.run(state)
                state = result.state
                if not result.success:
                    state.stage = MigrationStage.FAILED

            elif stage == MigrationStage.FETCH_RECORDS:
                state = self._stage_fetch_records(state)

            elif stage == MigrationStage.DRY_RUN:
                result = self.dry_run_agent.run(state)
                state = result.state
                if not result.success:
                    state.stage = MigrationStage.FAILED

            elif stage == MigrationStage.AWAIT_APPROVAL:
                result = self.approval_agent.run(state)
                state = result.state
                # Approval agent sets stage internally (MIGRATE or FAILED)

            elif stage == MigrationStage.MIGRATE:
                result = self.migration_agent.run(state)
                state = result.state
                if not result.success:
                    state.stage = MigrationStage.FAILED

            elif stage == MigrationStage.VALIDATE:
                result = self.validation_agent.run(state)
                state = result.state

            else:
                logger.warning("Unhandled stage: %s", stage)
                state.stage = MigrationStage.FAILED
                state.errors.append(f"Unhandled pipeline stage: {stage}")

        except Exception as exc:  # noqa: BLE001
            logger.error("Unhandled error in stage %s: %s", stage, exc, exc_info=True)
            state.stage = MigrationStage.FAILED
            state.errors.append(f"{stage}: {exc}")

        return state

    # ------------------------------------------------------------------
    # Individual stage implementations
    # ------------------------------------------------------------------

    def _stage_initialize(self, state: MigrationState) -> MigrationState:
        logger.info("Initialising migration pipeline …")
        state.stage = MigrationStage.FETCH_SCHEMA
        return state

    def _stage_fetch_records(self, state: MigrationState) -> MigrationState:
        logger.info("Fetching records from Cherwell …")
        raw_records = self.cherwell.get_all_records(
            object_type=self.migration_cfg.source_object_type,
            page_size=self.migration_cfg.batch_size,
        )

        migration_records = []
        for raw in raw_records:
            try:
                cherwell_record = self._normalise_cherwell_record(raw)
                migration_records.append(
                    MigrationRecord(source_record=cherwell_record)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping malformed record: %s", exc)
                state.warnings.append(f"Skipped malformed record: {exc}")

        state.records = migration_records
        state.total_records = len(migration_records)
        state.stage = MigrationStage.DRY_RUN
        logger.info("Fetched %d records", state.total_records)
        return state

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _authenticate(self) -> bool:
        cherwell_ok = self.cherwell.authenticate()
        servicenow_ok = self.servicenow.authenticate()
        if not cherwell_ok:
            logger.error("Cherwell authentication failed")
        if not servicenow_ok:
            logger.error("ServiceNow authentication failed")
        return cherwell_ok and servicenow_ok

    @staticmethod
    def _normalise_cherwell_record(raw: dict) -> CherwellRecord:
        """Map the raw Cherwell API dict to a typed CherwellRecord."""
        return CherwellRecord(
            rec_id=raw.get("RecID", str(uuid4()).replace("-", "")),
            incident_id=raw.get("IncidentID", "UNKNOWN"),
            short_description=raw.get("ShortDescription", ""),
            description=raw.get("Description", ""),
            priority=str(raw.get("Priority", "3")),
            status=raw.get("Status", "New"),
            category=raw.get("Category", ""),
            sub_category=raw.get("SubCategory", ""),
            owned_by=raw.get("OwnedBy", ""),
            owned_by_team=raw.get("OwnedByTeam", ""),
            created_date=raw.get("CreatedDateTime", ""),
            last_modified_date=raw.get("LastModifiedDateTime", ""),
            resolved_date=raw.get("ResolvedDateTime"),
            closed_date=raw.get("ClosedDateTime"),
            requester=raw.get("Requester", ""),
            customer=raw.get("Customer", ""),
            service=raw.get("Service", ""),
            impact=str(raw.get("Impact", "3")),
            urgency=str(raw.get("Urgency", "3")),
            raw_data=raw,
        )

    def _new_state(self) -> MigrationState:
        return MigrationState(is_mock_mode=self.migration_cfg.mock_mode)

    def _save_state(self, state: MigrationState) -> None:
        path = Path(self.migration_cfg.state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(state.model_dump_json(indent=2))
        logger.debug("State saved → %s", path)

    def _load_state(self, migration_id: str) -> MigrationState:
        path = Path(self.migration_cfg.state_file)
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            state = MigrationState(**data)
            if state.migration_id == migration_id:
                logger.info("Resuming migration %s from stage %s", migration_id, state.stage)
                return state
        logger.warning("Could not find saved state for ID %s – starting fresh", migration_id)
        return self._new_state()

    def _finalise(self, state: MigrationState) -> None:
        state.completed_at = datetime.now().isoformat()
        self._save_state(state)

        if state.stage == MigrationStage.COMPLETE:
            console.rule("[bold green]Migration Complete[/bold green]")
            if state.migration_result:
                print_migration_result(state.migration_result)
        else:
            console.rule("[bold red]Migration Failed[/bold red]")
            if state.errors:
                console.print("[red]Errors:[/red]")
                for e in state.errors:
                    console.print(f"  [red]✗[/red] {e}")

        report_path = save_json_report(state, self.migration_cfg.output_dir)
        console.print(f"\n[dim]Report saved → {report_path}[/dim]")
