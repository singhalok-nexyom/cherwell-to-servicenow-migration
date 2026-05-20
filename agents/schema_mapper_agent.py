"""
SchemaMappingAgent

Analyses the Cherwell source schema and the ServiceNow target schema,
then produces a SchemaMapping that drives all subsequent agents.
"""

from typing import Any, Dict, List

from agents.base_agent import AgentResult, BaseAgent
from connectors.cherwell_connector import CherwellConnector
from connectors.servicenow_connector import ServiceNowConnector
from models.data_models import FieldMapping, MigrationStage, MigrationState, SchemaMapping

# ------------------------------------------------------------------
# Static priority / status value maps
# ------------------------------------------------------------------

PRIORITY_MAP: Dict[str, str] = {
    "1": "1",   # Critical → Critical
    "2": "2",   # High     → High
    "3": "3",   # Medium   → Moderate
    "4": "4",   # Low      → Low
    "5": "5",   # Very Low → Planning
}

STATUS_MAP: Dict[str, str] = {
    "New": "1",
    "In Progress": "2",
    "Pending": "3",
    "Resolved": "6",
    "Closed": "7",
    "Cancelled": "8",
}

# Default field-level mapping expressed as
# (cherwell_field, servicenow_field, transform_key, required)
DEFAULT_FIELD_MAPPINGS: List[tuple] = [
    ("RecID",               "source_rec_id",      None,           True),
    ("ShortDescription",    "short_description",  None,           True),
    ("Description",         "description",        None,           False),
    ("Priority",            "priority",           "priority_map", False),
    ("Status",              "state",              "status_map",   False),
    ("Category",            "category",           None,           False),
    ("SubCategory",         "subcategory",        None,           False),
    ("OwnedBy",             "assigned_to",        None,           False),
    ("OwnedByTeam",         "assignment_group",   None,           False),
    ("CreatedDateTime",     "sys_created_on",     None,           False),
    ("LastModifiedDateTime","sys_updated_on",      None,           False),
    ("ResolvedDateTime",    "resolved_at",        None,           False),
    ("ClosedDateTime",      "closed_at",          None,           False),
    ("Requester",           "caller_id",          None,           False),
    ("Customer",            "caller_id",          None,           False),
    ("Service",             "business_service",   None,           False),
    ("Impact",              "impact",             None,           False),
    ("Urgency",             "urgency",            None,           False),
]


class SchemaMappingAgent(BaseAgent):
    """Fetches source/target schemas and generates the field-level mapping."""

    def __init__(
        self,
        cherwell: CherwellConnector,
        servicenow: ServiceNowConnector,
        log_level: str = "INFO",
    ) -> None:
        super().__init__("SchemaMappingAgent", log_level)
        self.cherwell = cherwell
        self.servicenow = servicenow

    # ------------------------------------------------------------------

    def run(self, state: MigrationState) -> AgentResult:
        self._info("=== Schema Mapping Agent started ===")

        try:
            # 1. Fetch schemas from both systems
            self._info("Fetching Cherwell schema …")
            src_schema = self.cherwell.get_schema(state.source_filter.get("object_type", "Incident") if state.source_filter else "Incident")
            state.source_schema = src_schema

            self._info("Fetching ServiceNow schema …")
            tgt_schema = self.servicenow.get_schema("incident")
            state.target_schema = tgt_schema

            # 2. Build field mapping
            mapping = self._build_mapping(src_schema, tgt_schema)
            state.schema_mapping = mapping
            state.stage = MigrationStage.FETCH_RECORDS

            self._info(
                "Schema mapping complete – %d field(s) mapped",
                len(mapping.field_mappings),
            )
            return AgentResult(success=True, state=state, message=f"{len(mapping.field_mappings)} fields mapped")

        except Exception as exc:  # noqa: BLE001
            self._error("Schema mapping failed: %s", exc)
            state.stage = MigrationStage.FAILED
            state.errors.append(f"SchemaMappingAgent: {exc}")
            return AgentResult(success=False, state=state, error=str(exc))

    # ------------------------------------------------------------------

    def _build_mapping(
        self,
        src_schema: Dict[str, Any],
        tgt_schema: Dict[str, Any],
    ) -> SchemaMapping:
        src_fields = {f["fieldId"] for f in src_schema.get("fields", [])}
        tgt_fields = {f["name"] for f in tgt_schema.get("fields", [])}

        field_mappings: List[FieldMapping] = []
        for src_f, tgt_f, transform, required in DEFAULT_FIELD_MAPPINGS:
            if src_f not in src_fields:
                self._warn("Source field '%s' not in schema – skipping", src_f)
                continue
            if tgt_f not in tgt_fields:
                self._warn("Target field '%s' not in schema – skipping", tgt_f)
                continue
            field_mappings.append(
                FieldMapping(
                    source_field=src_f,
                    target_field=tgt_f,
                    transform=transform,
                    required=required,
                )
            )

        # Always add migration-source marker
        field_mappings.append(
            FieldMapping(
                source_field="",
                target_field="u_migration_source",
                default_value="cherwell",
                description="Identifies records created by this migration",
            )
        )

        return SchemaMapping(
            source_table="Incident",
            target_table="incident",
            field_mappings=field_mappings,
        )

    # ------------------------------------------------------------------
    # Public helpers used by other agents
    # ------------------------------------------------------------------

    @staticmethod
    def apply_priority_map(value: str) -> str:
        return PRIORITY_MAP.get(str(value), "3")

    @staticmethod
    def apply_status_map(value: str) -> str:
        return STATUS_MAP.get(value, "1")
