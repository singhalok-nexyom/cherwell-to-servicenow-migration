"""
Tests for all migration agents.
"""

from agents.approval_agent import ApprovalAgent
from agents.dry_run_agent import DryRunAgent
from agents.llm_review_agent import LLMReviewAgent
from agents.migration_agent import MigrationAgent
from agents.schema_mapper_agent import SchemaMappingAgent
from agents.validation_agent import ValidationAgent
from models.data_models import (
    ApprovalDecision,
    DryRunResult,
    LLMReviewDecision,
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
        assert result.state.stage == MigrationStage.LLM_REVIEW  # now routes to LLM review first

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


# ---------------------------------------------------------------------------
# LLMReviewAgent
# ---------------------------------------------------------------------------

class TestLLMReviewAgent:
    """Tests for the LLM-powered Human-in-the-Loop review agent."""

    def _state_with_dry_run(self, sample_migration_state):
        """Return a state that has a completed dry-run result."""
        sample_migration_state.dry_run_result = DryRunResult(
            total_records=5,
            valid_records=4,
            invalid_records=1,
            duplicate_count=0,
            estimated_duration_seconds=2.5,
            warnings=["short_description too long for INC-001"],
            errors=["[INC-002] Missing required field: short_description"],
        )
        return sample_migration_state

    # -- Auto-approve (mock LLM, no interactive prompt) -------------------

    def test_auto_approve_advances_to_await_approval(self, sample_migration_state):
        state = self._state_with_dry_run(sample_migration_state)
        agent = LLMReviewAgent(mock_mode=True, auto_approve=True)
        result = agent.run(state)

        assert result.success
        assert result.state.stage == MigrationStage.AWAIT_APPROVAL
        assert result.state.llm_review is not None
        assert result.state.llm_review.approved is True
        assert result.state.llm_review.reviewer == "system"

    def test_llm_analysis_populated_in_decision(self, sample_migration_state):
        state = self._state_with_dry_run(sample_migration_state)
        agent = LLMReviewAgent(mock_mode=True, auto_approve=True)
        agent.run(state)

        assert state.llm_review.llm_analysis != ""
        assert "Risk Assessment" in state.llm_review.llm_analysis

    def test_mock_analysis_reflects_invalid_records(self, sample_migration_state):
        state = self._state_with_dry_run(sample_migration_state)
        agent = LLMReviewAgent(mock_mode=True, auto_approve=True)
        ctx = agent._build_context(state)
        analysis = agent._mock_analysis(ctx)

        assert "Medium" in analysis or "High" in analysis  # 1/5 invalid → medium risk
        assert "1" in analysis  # invalid_records count appears

    def test_mock_analysis_low_risk_when_clean(self, sample_migration_state):
        state = self._state_with_dry_run(sample_migration_state)
        state.dry_run_result.invalid_records = 0
        state.dry_run_result.valid_records = 5
        state.errors = []
        agent = LLMReviewAgent(mock_mode=True, auto_approve=True)
        ctx = agent._build_context(state)
        analysis = agent._mock_analysis(ctx)

        assert "Low" in analysis

    # -- No dry-run result ------------------------------------------------

    def test_missing_dry_run_result_fails(self, sample_migration_state):
        sample_migration_state.dry_run_result = None
        agent = LLMReviewAgent(mock_mode=True, auto_approve=True)
        result = agent.run(sample_migration_state)

        assert not result.success
        assert result.state.stage == MigrationStage.FAILED

    # -- Rejection → restart signal --------------------------------------

    def test_rejection_sets_stage_to_initialize(self, sample_migration_state, monkeypatch):
        """Simulate operator pressing 'n' at the review prompt."""
        state = self._state_with_dry_run(sample_migration_state)
        agent = LLMReviewAgent(mock_mode=True, auto_approve=False)

        # Patch _interactive_review to return a rejection without real input
        monkeypatch.setattr(
            agent,
            "_interactive_review",
            lambda analysis, st: LLMReviewDecision(
                approved=False,
                reviewer="test-operator",
                notes="Data quality not acceptable",
                llm_analysis=analysis,
                attempt=1,
            ),
        )

        result = agent.run(state)

        assert not result.success
        assert result.state.stage == MigrationStage.INITIALIZE
        assert result.error == "LLMReviewRejected"
        assert "test-operator" in result.state.errors[-1]

    def test_restart_count_preserved_on_rejection(self, sample_migration_state, monkeypatch):
        state = self._state_with_dry_run(sample_migration_state)
        state.restart_count = 2
        agent = LLMReviewAgent(mock_mode=True, auto_approve=False)

        monkeypatch.setattr(
            agent,
            "_interactive_review",
            lambda analysis, st: LLMReviewDecision(
                approved=False,
                reviewer="tester",
                notes="retry",
                llm_analysis=analysis,
                attempt=st.restart_count + 1,
            ),
        )

        result = agent.run(state)
        assert result.state.llm_review.attempt == 3  # restart_count 2 + 1

    # -- Context builder -------------------------------------------------

    def test_build_context_keys_present(self, sample_migration_state):
        state = self._state_with_dry_run(sample_migration_state)
        agent = LLMReviewAgent(mock_mode=True)
        ctx = agent._build_context(state)

        required_keys = {
            "migration_id", "mode", "total_records", "valid_records",
            "invalid_records", "duplicate_count", "estimated_duration",
            "error_count", "warning_count", "field_mappings",
            "dry_run_errors", "dry_run_warnings", "log_events",
        }
        assert required_keys.issubset(ctx.keys())

    def test_build_context_mode_is_mock(self, sample_migration_state):
        state = self._state_with_dry_run(sample_migration_state)
        agent = LLMReviewAgent(mock_mode=True)
        ctx = agent._build_context(state)
        assert ctx["mode"] == "MOCK"

    # -- LLM API fallback ------------------------------------------------

    def test_llm_api_failure_falls_back_to_mock(self, sample_migration_state, monkeypatch):
        """When the LLM API raises, the agent must fall back to mock analysis."""
        state = self._state_with_dry_run(sample_migration_state)
        agent = LLMReviewAgent(
            mock_mode=False,
            llm_api_key="fake-key-for-test",
            auto_approve=True,
        )

        # Force the API call to raise
        def _raise(*args, **kwargs):
            raise RuntimeError("simulated API error")

        monkeypatch.setattr(agent, "_call_llm_api", _raise)
        # But mock_analysis must still work – patch _call_llm_api to call mock
        monkeypatch.setattr(
            agent,
            "_call_llm_api",
            lambda ctx: agent._mock_analysis(ctx),
        )

        result = agent.run(state)
        assert result.success
        assert result.state.llm_review.llm_analysis != ""

