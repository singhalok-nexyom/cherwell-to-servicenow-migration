"""
MigrationAgent

Performs the actual data migration from Cherwell to ServiceNow in
configurable batches, with per-record error handling and retry support.
Records that fail all retries are logged and skipped so the batch
always continues.
"""

import time
from datetime import datetime
from typing import List

from agents.base_agent import AgentResult, BaseAgent
from agents.dry_run_agent import DryRunAgent
from connectors.servicenow_connector import ServiceNowConnector
from models.data_models import (
    MigrationRecord,
    MigrationResult,
    MigrationStage,
    MigrationState,
    RecordStatus,
)
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
)


class MigrationAgent(BaseAgent):
    """Executes the approved migration batch-by-batch."""

    def __init__(
        self,
        servicenow: ServiceNowConnector,
        batch_size: int = 50,
        max_retries: int = 3,
        log_level: str = "INFO",
    ) -> None:
        super().__init__("MigrationAgent", log_level)
        self.servicenow = servicenow
        self.batch_size = batch_size
        self.max_retries = max_retries
        self._dry_run_agent = DryRunAgent(servicenow, log_level)

    # ------------------------------------------------------------------

    def run(self, state: MigrationState) -> AgentResult:
        self._info("=== Migration Agent started ===")

        if not state.approval or not state.approval.approved:
            err = "Migration not approved – aborting"
            state.errors.append(err)
            state.stage = MigrationStage.FAILED
            return AgentResult(success=False, state=state, error=err)

        # Only migrate records that passed dry-run validation
        pending = [r for r in state.records if r.status == RecordStatus.PENDING]
        self._info("Records to migrate: %d (batch size: %d)", len(pending), self.batch_size)

        result = MigrationResult(total_records=len(pending))
        start = time.monotonic()
        batches = self._batch(pending, self.batch_size)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Migrating records …", total=len(pending))

            for batch_num, batch in enumerate(batches, start=1):
                self._info("Processing batch %d (%d records)", batch_num, len(batch))
                self._migrate_batch(batch, result, progress, task)
                result.batches_processed += 1

        result.duration_seconds = time.monotonic() - start

        state.migration_result = result
        state.stage = MigrationStage.VALIDATE

        self._info(
            "Migration complete – success=%d  failed=%d  skipped=%d  duration=%.1fs",
            result.successful,
            result.failed,
            result.skipped,
            result.duration_seconds,
        )
        return AgentResult(
            success=True,
            state=state,
            message=(
                f"Migrated {result.successful}/{result.total_records} records "
                f"in {result.duration_seconds:.1f}s"
            ),
        )

    # ------------------------------------------------------------------

    def _migrate_batch(
        self,
        batch: List[MigrationRecord],
        result: MigrationResult,
        progress: Progress,
        task_id: int,
    ) -> None:
        for mr in batch:
            if mr.target_record is None:
                # Re-transform if target wasn't set (shouldn't happen normally)
                try:
                    mr.target_record = self._dry_run_agent._transform(
                        mr.source_record,
                        self._get_placeholder_state(),
                    )
                except Exception as exc:  # noqa: BLE001
                    mr.status = RecordStatus.FAILED
                    mr.error_message = f"Transform error: {exc}"
                    result.failed += 1
                    result.failed_record_ids.append(mr.source_record.rec_id)
                    progress.advance(task_id)
                    continue

            success = self._create_with_retry(mr, result)
            if not success:
                result.failed_record_ids.append(mr.source_record.rec_id)
            progress.advance(task_id)

    def _create_with_retry(self, mr: MigrationRecord, result: MigrationResult) -> bool:
        for attempt in range(1, self.max_retries + 1):
            try:
                mr.status = RecordStatus.PROCESSING
                payload = mr.target_record.model_dump(exclude={"sys_id", "number"})
                created = self.servicenow.create_record("incident", payload)
                mr.target_record.sys_id = created.get("sys_id")
                mr.target_record.number = created.get("number")
                mr.status = RecordStatus.SUCCESS
                mr.migrated_at = datetime.now().isoformat()
                result.successful += 1
                return True
            except Exception as exc:  # noqa: BLE001
                self._warn(
                    "Attempt %d/%d failed for %s: %s",
                    attempt,
                    self.max_retries,
                    mr.source_record.incident_id,
                    exc,
                )
                mr.retry_count = attempt
                if attempt == self.max_retries:
                    mr.status = RecordStatus.FAILED
                    mr.error_message = str(exc)
                    result.failed += 1
                    return False
                time.sleep(2 ** attempt)  # exponential back-off
        return False

    # ------------------------------------------------------------------

    @staticmethod
    def _batch(records: List[MigrationRecord], size: int) -> List[List[MigrationRecord]]:
        return [records[i: i + size] for i in range(0, len(records), size)]

    @staticmethod
    def _get_placeholder_state() -> MigrationState:
        """Returns a minimal state used only by the inline re-transform path."""
        from models.data_models import MigrationState, SchemaMapping, FieldMapping
        return MigrationState(schema_mapping=SchemaMapping())
