"""
ValidationAgent

After the migration completes, cross-checks that every record marked
SUCCESS in the state can be retrieved from ServiceNow and that key
fields match the source values.
"""

from typing import List, Tuple

from agents.base_agent import AgentResult, BaseAgent
from connectors.servicenow_connector import ServiceNowConnector
from models.data_models import MigrationRecord, MigrationStage, MigrationState, RecordStatus


class ValidationAgent(BaseAgent):
    """Validates migrated records against the ServiceNow target."""

    def __init__(self, servicenow: ServiceNowConnector, log_level: str = "INFO") -> None:
        super().__init__("ValidationAgent", log_level)
        self.servicenow = servicenow

    # ------------------------------------------------------------------

    def run(self, state: MigrationState) -> AgentResult:
        self._info("=== Validation Agent started ===")

        migrated = [r for r in state.records if r.status == RecordStatus.SUCCESS]
        self._info("Validating %d migrated record(s) …", len(migrated))

        passed = 0
        failed = 0
        issues: List[str] = []

        for mr in migrated:
            ok, errs = self._validate_record(mr)
            mr.validation_passed = ok
            if ok:
                passed += 1
            else:
                failed += 1
                for e in errs:
                    issues.append(f"[{mr.source_record.incident_id}] {e}")

        # Reconciliation counts
        src_total = state.total_records
        mig_total = state.migration_result.successful if state.migration_result else 0
        skipped = len([r for r in state.records if r.status == RecordStatus.SKIPPED])

        self._info(
            "Validation – passed=%d  failed=%d | source=%d migrated=%d skipped=%d",
            passed,
            failed,
            src_total,
            mig_total,
            skipped,
        )

        if issues:
            for issue in issues:
                self._warn(issue)
            state.warnings.extend(issues)

        state.stage = MigrationStage.COMPLETE
        msg = (
            f"Validation complete: {passed} passed, {failed} failed "
            f"(source={src_total}, migrated={mig_total}, skipped={skipped})"
        )
        return AgentResult(success=True, state=state, message=msg)

    # ------------------------------------------------------------------

    def _validate_record(self, mr: MigrationRecord) -> Tuple[bool, List[str]]:
        errors: List[str] = []

        if not mr.target_record or not mr.target_record.sys_id:
            errors.append("No sys_id – record may not have been created")
            return False, errors

        # In mock mode the record lives in the in-memory store
        sn_records = self.servicenow.get_records(
            "incident",
            filters={"source_rec_id": mr.source_record.rec_id},
        )

        if not sn_records:
            errors.append(
                f"Record with source_rec_id={mr.source_record.rec_id} not found in ServiceNow"
            )
            return False, errors

        sn = sn_records[0]

        # Field-level checks
        if sn.get("short_description", "") != mr.source_record.short_description:
            errors.append("short_description mismatch")

        if sn.get("source_rec_id", "") != mr.source_record.rec_id:
            errors.append("source_rec_id mismatch")

        return len(errors) == 0, errors
