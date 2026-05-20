"""
Data models for the Cherwell → ServiceNow migration tool.
All models use Pydantic v2 for validation and serialisation.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MigrationStage(str, Enum):
    """Sequential stages of the migration pipeline."""
    INITIALIZE = "initialize"
    FETCH_SCHEMA = "fetch_schema"
    MAP_SCHEMA = "map_schema"
    FETCH_RECORDS = "fetch_records"
    DRY_RUN = "dry_run"
    AWAIT_APPROVAL = "await_approval"
    MIGRATE = "migrate"
    VALIDATE = "validate"
    COMPLETE = "complete"
    FAILED = "failed"


class RecordStatus(str, Enum):
    """Per-record migration status."""
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Source / target record shapes
# ---------------------------------------------------------------------------

class CherwellRecord(BaseModel):
    """Normalised representation of a Cherwell Incident record."""
    rec_id: str
    incident_id: str
    short_description: str
    description: str = ""
    priority: str = "3"
    status: str = "New"
    category: str = ""
    sub_category: str = ""
    owned_by: str = ""
    owned_by_team: str = ""
    created_date: str = ""
    last_modified_date: str = ""
    resolved_date: Optional[str] = None
    closed_date: Optional[str] = None
    requester: str = ""
    customer: str = ""
    service: str = ""
    impact: str = "3"
    urgency: str = "3"
    raw_data: Dict[str, Any] = Field(default_factory=dict)


class ServiceNowRecord(BaseModel):
    """Normalised representation of a ServiceNow Incident record."""
    sys_id: Optional[str] = None
    number: Optional[str] = None
    short_description: str
    description: str = ""
    priority: str = "3"
    state: str = "1"
    category: str = ""
    subcategory: str = ""
    assigned_to: str = ""
    assignment_group: str = ""
    sys_created_on: str = ""
    sys_updated_on: str = ""
    resolved_at: Optional[str] = None
    closed_at: Optional[str] = None
    caller_id: str = ""
    business_service: str = ""
    impact: str = "3"
    urgency: str = "3"
    # Traceability back to source
    source_rec_id: str = ""
    u_migration_source: str = "cherwell"


# ---------------------------------------------------------------------------
# Schema mapping models
# ---------------------------------------------------------------------------

class FieldMapping(BaseModel):
    """Maps one source field to one target field, with optional transform."""
    source_field: str
    target_field: str
    transform: Optional[str] = None   # e.g. "priority_map", "status_map"
    required: bool = False
    default_value: Optional[Any] = None
    description: str = ""


class SchemaMapping(BaseModel):
    """Complete field-level mapping between source and target schemas."""
    source_system: str = "cherwell"
    target_system: str = "servicenow"
    entity_type: str = "incident"
    source_table: str = "Incident"
    target_table: str = "incident"
    field_mappings: List[FieldMapping] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    version: str = "1.0"


# ---------------------------------------------------------------------------
# Pipeline state models
# ---------------------------------------------------------------------------

class MigrationRecord(BaseModel):
    """Tracks the migration lifecycle of a single record."""
    source_record: CherwellRecord
    target_record: Optional[ServiceNowRecord] = None
    status: RecordStatus = RecordStatus.PENDING
    error_message: Optional[str] = None
    migrated_at: Optional[str] = None
    validation_passed: Optional[bool] = None
    retry_count: int = 0


class DryRunResult(BaseModel):
    """Aggregated results of the dry-run simulation."""
    total_records: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    sample_transformed: List[ServiceNowRecord] = Field(default_factory=list)
    estimated_duration_seconds: float = 0.0
    duplicate_count: int = 0
    field_coverage: Dict[str, int] = Field(default_factory=dict)


class MigrationResult(BaseModel):
    """Final statistics after the migration completes."""
    total_records: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    failed_record_ids: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    batches_processed: int = 0


class ApprovalDecision(BaseModel):
    """Records the human operator's approval/rejection decision."""
    approved: bool
    approver: str = "operator"
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    notes: Optional[str] = None


class MigrationState(BaseModel):
    """Immutable-ish snapshot of the full migration pipeline state.

    Serialised to JSON on disk after every stage so the run can be resumed.
    """
    migration_id: str = Field(default_factory=lambda: str(uuid4()))
    stage: MigrationStage = MigrationStage.INITIALIZE
    source_schema: Optional[Dict[str, Any]] = None
    target_schema: Optional[Dict[str, Any]] = None
    schema_mapping: Optional[SchemaMapping] = None
    records: List[MigrationRecord] = Field(default_factory=list)
    total_records: int = 0
    dry_run_result: Optional[DryRunResult] = None
    approval: Optional[ApprovalDecision] = None
    migration_result: Optional[MigrationResult] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    is_mock_mode: bool = True
    source_filter: Optional[Dict[str, Any]] = None
