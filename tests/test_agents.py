"""
Tests for all migration agents.
"""

from agents.approval_agent import ApprovalAgent
from agents.dry_run_agent import DryRunAgent
from agents.migration_agent import MigrationAgent
from agents.schema_mapper_agent import SchemaMappingAgent
from agents.validation_agent import ValidationAgent
from models.data_models import (
    ApprovalDecision,
    MigrationStage,
    RecordStatus,
)


# ---------------------------------------------------------------------------
# SchemaMappingAgent
# ---------------------------------------------------------------------------

class TestSchemaMappingAgent:
    def test_run_success(self, cherwell_connector, servicenow_connector, sample_migration_state):
        agent = SchemaMappingAgent(cherwell_connector, servicenow_connector)
        # Start from INITIALIZE stage to simulate fetching schema
        sample_migration_state.schema_mapping = None
        result = agent.run(sample_migration_state)

        assert result.success
        assert result.state.schema_mapping is not None
        assert len(result.state.schema_mapping.field_mappings) > 0

    def test_priority_map(self):
        assert SchemaMappingAgent.apply_priority_map("1") == "1"
        assert SchemaMappingAgent.apply_priority_map("3") == "3"
        assert SchemaMappingAgent.apply_priority_map("99") == "3"  # default

    def test_status_map(self):
        assert SchemaMappingAgent.apply_status_map("New") == "1"
        assert SchemaMappingAgent.apply_status_map("Resolved") == "6"
        assert SchemaMappingAgent.apply_status_map("Unknown") == "1"  # default


# ---------------------------------------------------------------------------
# DryRunAgent
# ---------------------------------------------------------------------------

class TestDryRunAgent:
    def test_run_success(self, servicenow_connector, sample_migration_state):
        agent = DryRunAgent(servicenow_connector)
        result = agent.run(sample_migration_state)

        assert result.success
        assert result.state.dry_run_result is not None
        assert result.state.dry_run_result.total_records == 1
        assert result.state.stage == MigrationStage.AWAIT_APPROVAL

    def test_run_no_mapping_fails(self, servicenow_connector, sample_migration_state):
        sample_migration_state.schema_mapping = None
        agent = DryRunAgent(servicenow_connector)
        result = agent.run(sample_migration_state)

        assert not result.success
        assert result.state.stage == MigrationStage.FAILED

    def test_valid_record_counted(self, servicenow_connector, sample_migration_state):
        agent = DryRunAgent(servicenow_connector)
        result = agent.run(sample_migration_state)

        assert result.state.dry_run_result.valid_records >= 1

    def test_transform_produces_servicenow_record(
        self, servicenow_connector, sample_migration_state, sample_cherwell_record
    ):
        agent = DryRunAgent(servicenow_connector)
        sn = agent._transform(sample_cherwell_record, sample_migration_state)

        assert sn.short_description == sample_cherwell_record.short_description
        assert sn.source_rec_id == sample_cherwell_record.rec_id
        assert sn.u_migration_source == "cherwell"


# ---------------------------------------------------------------------------
# ApprovalAgent
# ---------------------------------------------------------------------------

class TestApprovalAgent:
    def test_auto_approve(self, sample_migration_state):
        from models.data_models import DryRunResult
        sample_migration_state.dry_run_result = DryRunResult(
            total_records=1, valid_records=1, invalid_records=0
        )
        agent = ApprovalAgent(auto_approve=True)
        result = agent.run(sample_migration_state)

        assert result.success
        assert result.state.approval is not None
        assert result.state.approval.approved is True
        assert result.state.stage == MigrationStage.MIGRATE

    def test_no_dry_run_result_fails(self, sample_migration_state):
        sample_migration_state.dry_run_result = None
        agent = ApprovalAgent(auto_approve=True)
        result = agent.run(sample_migration_state)

        assert not result.success
        assert result.state.stage == MigrationStage.FAILED


# ---------------------------------------------------------------------------
# MigrationAgent
# ---------------------------------------------------------------------------

class TestMigrationAgent:
    def _approved_state(self, sample_migration_state):
        """Helper: put a state into MIGRATE stage with approval."""
        from models.data_models import DryRunResult
        sample_migration_state.dry_run_result = DryRunResult(
            total_records=1, valid_records=1, invalid_records=0
        )
        # Pre-populate target record via dry-run
        from agents.dry_run_agent import DryRunAgent
        from connectors.servicenow_connector import ServiceNowConnector
        from config.settings import ServiceNowConfig
        sn = ServiceNowConnector(ServiceNowConfig(), mock_mode=True)
        dr = DryRunAgent(sn)
        dr.run(sample_migration_state)
        sample_migration_state.approval = ApprovalDecision(approved=True, approver="tester")
        return sample_migration_state

    def test_migrates_approved_records(
        self, servicenow_connector, sample_migration_state
    ):
        state = self._approved_state(sample_migration_state)
        agent = MigrationAgent(servicenow_connector, batch_size=5)
        result = agent.run(state)

        assert result.success
        assert result.state.migration_result is not None
        assert result.state.migration_result.successful >= 1

    def test_no_approval_fails(self, servicenow_connector, sample_migration_state):
        agent = MigrationAgent(servicenow_connector)
        result = agent.run(sample_migration_state)

        assert not result.success
        assert result.state.stage == MigrationStage.FAILED


# ---------------------------------------------------------------------------
# ValidationAgent
# ---------------------------------------------------------------------------

class TestValidationAgent:
    def test_validates_migrated_records(
        self, servicenow_connector, sample_migration_state, sample_cherwell_record
    ):
        # Simulate a successfully migrated record already in ServiceNow mock store
        payload = {
            "short_description": sample_cherwell_record.short_description,
            "source_rec_id": sample_cherwell_record.rec_id,
        }
        created = servicenow_connector.create_record("incident", payload)

        from models.data_models import ServiceNowRecord
        mr = sample_migration_state.records[0]
        mr.status = RecordStatus.SUCCESS
        mr.target_record = ServiceNowRecord(
            sys_id=created["sys_id"],
            short_description=sample_cherwell_record.short_description,
            source_rec_id=sample_cherwell_record.rec_id,
        )

        agent = ValidationAgent(servicenow_connector)
        result = agent.run(sample_migration_state)

        assert result.success
        assert result.state.stage == MigrationStage.COMPLETE
