"""
DryRunAgent

Transforms every fetched record using the schema mapping and validates
the result WITHOUT writing anything to ServiceNow.
Produces a DryRunResult that is presented to the human approver.
"""

import time
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentResult, BaseAgent
from agents.schema_mapper_agent import SchemaMappingAgent
from connectors.servicenow_connector import ServiceNowConnector
from models.data_models import (
    CherwellRecord,
    DryRunResult,
    MigrationRecord,
    MigrationStage,
    MigrationState,
    RecordStatus,
    ServiceNowRecord,
)


class DryRunAgent(BaseAgent):
    """Validates and transforms all records without touching the target system."""

    def __init__(
        self,
        servicenow: ServiceNowConnector,
        log_level: str = "INFO",
    ) -> None:
        super().__init__("DryRunAgent", log_level)
        self.servicenow = servicenow

    # ------------------------------------------------------------------

    def run(self, state: MigrationState) -> AgentResult:
        self._info("=== Dry Run Agent started ===")

        if not state.schema_mapping:
            err = "No schema mapping available – run SchemaMappingAgent first"
            state.errors.append(err)
            state.stage = MigrationStage.FAILED
            return AgentResult(success=False, state=state, error=err)

        start = time.monotonic()
        result = DryRunResult(total_records=len(state.records))
        sample_limit = 5

        for mr in state.records:
            try:
                sn_record = self._transform(mr.source_record, state)
                validation_errors = self._validate(sn_record, state)

                # Duplicate check
                is_dup = self.servicenow.check_duplicate(
                    "incident", "source_rec_id", mr.source_record.rec_id
                )
                if is_dup:
                    result.duplicate_count += 1
                    result.warnings.append(
                        f"Possible duplicate: {mr.source_record.incident_id} "
                        f"(rec_id={mr.source_record.rec_id}) already exists in ServiceNow"
                    )
                    mr.status = RecordStatus.SKIPPED
                elif validation_errors:
                    result.invalid_records += 1
                    for ve in validation_errors:
                        result.errors.append(
                            f"[{mr.source_record.incident_id}] {ve}"
                        )
                    mr.status = RecordStatus.FAILED
                else:
                    result.valid_records += 1
                    mr.target_record = sn_record
                    mr.status = RecordStatus.PENDING
                    if len(result.sample_transformed) < sample_limit:
                        result.sample_transformed.append(sn_record)

            except Exception as exc:  # noqa: BLE001
                result.invalid_records += 1
                result.errors.append(f"[{mr.source_record.incident_id}] Transform error: {exc}")
                mr.status = RecordStatus.FAILED

        elapsed = time.monotonic() - start
        # Rough estimate: real migration ≈ 0.5 s per record
        result.estimated_duration_seconds = result.valid_records * 0.5
        result.field_coverage = self._field_coverage(state)

        state.dry_run_result = result
        state.stage = MigrationStage.AWAIT_APPROVAL

        self._info(
            "Dry run complete – valid=%d  invalid=%d  duplicates=%d",
            result.valid_records,
            result.invalid_records,
            result.duplicate_count,
        )
        return AgentResult(
            success=True,
            state=state,
            message=(
                f"Dry run: {result.valid_records} valid, "
                f"{result.invalid_records} invalid, "
                f"{result.duplicate_count} duplicates"
            ),
        )

    # ------------------------------------------------------------------
    # Transform helpers
    # ------------------------------------------------------------------

    def _transform(
        self, src: CherwellRecord, state: MigrationState
    ) -> ServiceNowRecord:
        mapping = state.schema_mapping
        raw = src.raw_data if src.raw_data else src.model_dump()

        field_values: Dict[str, Any] = {}
        for fm in mapping.field_mappings:
            if fm.default_value is not None and not fm.source_field:
                field_values[fm.target_field] = fm.default_value
                continue
            raw_val = raw.get(fm.source_field) or getattr(src, self._snake(fm.source_field), fm.default_value)
            if raw_val is None:
                raw_val = fm.default_value
            # Apply transforms
            if fm.transform == "priority_map":
                raw_val = SchemaMappingAgent.apply_priority_map(str(raw_val or "3"))
            elif fm.transform == "status_map":
                raw_val = SchemaMappingAgent.apply_status_map(str(raw_val or "New"))
            field_values[fm.target_field] = raw_val

        return ServiceNowRecord(
            short_description=field_values.get("short_description", src.short_description),
            description=field_values.get("description", src.description),
            priority=str(field_values.get("priority", "3")),
            state=str(field_values.get("state", "1")),
            category=field_values.get("category", src.category),
            subcategory=field_values.get("subcategory", src.sub_category),
            assigned_to=field_values.get("assigned_to", src.owned_by),
            assignment_group=field_values.get("assignment_group", src.owned_by_team),
            sys_created_on=field_values.get("sys_created_on", src.created_date),
            sys_updated_on=field_values.get("sys_updated_on", src.last_modified_date),
            resolved_at=field_values.get("resolved_at", src.resolved_date),
            closed_at=field_values.get("closed_at", src.closed_date),
            caller_id=field_values.get("caller_id", src.requester),
            business_service=field_values.get("business_service", src.service),
            impact=str(field_values.get("impact", src.impact)),
            urgency=str(field_values.get("urgency", src.urgency)),
            source_rec_id=src.rec_id,
            u_migration_source="cherwell",
        )

    @staticmethod
    def _snake(camel: str) -> str:
        """Very lightweight CamelCase → snake_case for attribute lookup."""
        import re
        return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).lower()

    @staticmethod
    def _validate(record: ServiceNowRecord, state: MigrationState) -> List[str]:
        errors: List[str] = []
        if not record.short_description:
            errors.append("short_description is required but empty")
        if not record.source_rec_id:
            errors.append("source_rec_id must not be empty")
        if record.priority not in {"1", "2", "3", "4", "5"}:
            errors.append(f"Invalid priority value: {record.priority}")
        return errors

    @staticmethod
    def _field_coverage(state: MigrationState) -> Dict[str, int]:
        """Count how many records have each target field populated."""
        coverage: Dict[str, int] = {}
        for mr in state.records:
            if mr.target_record:
                for k, v in mr.target_record.model_dump().items():
                    if v:
                        coverage[k] = coverage.get(k, 0) + 1
        return coverage
