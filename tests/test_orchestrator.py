"""
Integration tests for the MigrationOrchestrator.
"""

import json
from pathlib import Path

from orchestrator.orchestrator import MigrationOrchestrator
from models.data_models import MigrationStage


class TestMigrationOrchestrator:
    """End-to-end orchestrator tests running in mock + auto-approve mode."""

    def test_full_pipeline_completes(self, cherwell_config, servicenow_config, migration_config, tmp_path):
        migration_config.output_dir = str(tmp_path)
        migration_config.state_file = str(tmp_path / "state.json")
        migration_config.auto_approve = True
        migration_config.mock_mode = True

        orch = MigrationOrchestrator(cherwell_config, servicenow_config, migration_config)
        state = orch.run()

        assert state.stage == MigrationStage.COMPLETE
        assert state.migration_result is not None
        assert state.migration_result.successful > 0

    def test_llm_review_decision_populated(self, cherwell_config, servicenow_config, migration_config, tmp_path):
        """Verify that the LLM review decision is recorded in the final state."""
        migration_config.output_dir = str(tmp_path)
        migration_config.state_file = str(tmp_path / "state.json")
        migration_config.auto_approve = True
        migration_config.mock_mode = True

        orch = MigrationOrchestrator(cherwell_config, servicenow_config, migration_config)
        state = orch.run()

        assert state.llm_review is not None
        assert state.llm_review.approved is True
        assert state.llm_review.llm_analysis != ""

    def test_pipeline_restart_on_llm_rejection(
        self, cherwell_config, servicenow_config, migration_config, tmp_path, monkeypatch
    ):
        """LLM review rejection should restart the pipeline; second attempt auto-approves."""
        migration_config.output_dir = str(tmp_path)
        migration_config.state_file = str(tmp_path / "state.json")
        migration_config.auto_approve = True   # approval_agent stays auto-approve throughout
        migration_config.mock_mode = True

        orch = MigrationOrchestrator(cherwell_config, servicenow_config, migration_config)

        call_count = {"n": 0}
        original_run = orch.llm_review_agent.run

        def patched_run(state):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call: simulate rejection
                from models.data_models import LLMReviewDecision
                from agents.base_agent import AgentResult
                state.llm_review = LLMReviewDecision(
                    approved=False,
                    reviewer="tester",
                    notes="First attempt rejected",
                    llm_analysis="mock",
                    attempt=1,
                )
                state.stage = MigrationStage.INITIALIZE
                return AgentResult(
                    success=False, state=state,
                    message="Rejected", error="LLMReviewRejected",
                )
            # Second call: approve normally (auto_approve already True)
            return original_run(state)

        monkeypatch.setattr(orch.llm_review_agent, "run", patched_run)

        state = orch.run()

        assert call_count["n"] == 2
        assert state.restart_count == 1
        assert state.stage == MigrationStage.COMPLETE

    def test_state_persisted_to_disk(self, cherwell_config, servicenow_config, migration_config, tmp_path):
        migration_config.output_dir = str(tmp_path)
        migration_config.state_file = str(tmp_path / "state.json")
        migration_config.auto_approve = True
        migration_config.mock_mode = True

        orch = MigrationOrchestrator(cherwell_config, servicenow_config, migration_config)
        orch.run()

        assert Path(migration_config.state_file).exists()
        with open(migration_config.state_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["stage"] == "complete"

    def test_report_file_created(self, cherwell_config, servicenow_config, migration_config, tmp_path):
        migration_config.output_dir = str(tmp_path)
        migration_config.state_file = str(tmp_path / "state.json")
        migration_config.auto_approve = True
        migration_config.mock_mode = True

        orch = MigrationOrchestrator(cherwell_config, servicenow_config, migration_config)
        orch.run()

        reports = list(tmp_path.glob("migration_report_*.json"))
        assert len(reports) >= 1

    def test_schema_mapping_populated(self, cherwell_config, servicenow_config, migration_config, tmp_path):
        migration_config.output_dir = str(tmp_path)
        migration_config.state_file = str(tmp_path / "state.json")
        migration_config.auto_approve = True
        migration_config.mock_mode = True

        orch = MigrationOrchestrator(cherwell_config, servicenow_config, migration_config)
        state = orch.run()

        assert state.schema_mapping is not None
        assert len(state.schema_mapping.field_mappings) > 0

    def test_dry_run_result_populated(self, cherwell_config, servicenow_config, migration_config, tmp_path):
        migration_config.output_dir = str(tmp_path)
        migration_config.state_file = str(tmp_path / "state.json")
        migration_config.auto_approve = True
        migration_config.mock_mode = True

        orch = MigrationOrchestrator(cherwell_config, servicenow_config, migration_config)
        state = orch.run()

        assert state.dry_run_result is not None
        assert state.dry_run_result.total_records > 0

    def test_all_records_have_status(self, cherwell_config, servicenow_config, migration_config, tmp_path):
        migration_config.output_dir = str(tmp_path)
        migration_config.state_file = str(tmp_path / "state.json")
        migration_config.auto_approve = True
        migration_config.mock_mode = True

        orch = MigrationOrchestrator(cherwell_config, servicenow_config, migration_config)
        state = orch.run()

        for record in state.records:
            assert record.status is not None

