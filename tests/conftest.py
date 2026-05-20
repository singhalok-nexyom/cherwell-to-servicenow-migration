"""
Shared pytest fixtures for the migration tool test suite.
"""

import pytest

from config.settings import CherwellConfig, MigrationConfig, ServiceNowConfig
from connectors.cherwell_connector import CherwellConnector
from connectors.servicenow_connector import ServiceNowConnector
from models.data_models import (
    CherwellRecord,
    FieldMapping,
    MigrationRecord,
    MigrationState,
    SchemaMapping,
)


@pytest.fixture
def cherwell_config() -> CherwellConfig:
    return CherwellConfig(
        base_url="https://mock-cherwell.example.com/CherwellAPI",
        client_id="test-client-id",
        username="test-user",
        password="test-password",  # noqa: S106  # test credential
    )


@pytest.fixture
def servicenow_config() -> ServiceNowConfig:
    return ServiceNowConfig(
        instance_url="https://mock-instance.service-now.com",
        username="test-user",
        password="test-password",  # noqa: S106  # test credential
    )


@pytest.fixture
def migration_config() -> MigrationConfig:
    return MigrationConfig(
        mock_mode=True,
        batch_size=5,
        auto_approve=True,
        output_dir="./test_output",
        state_file="./test_output/test_state.json",
    )


@pytest.fixture
def cherwell_connector(cherwell_config) -> CherwellConnector:
    return CherwellConnector(cherwell_config, mock_mode=True)


@pytest.fixture
def servicenow_connector(servicenow_config) -> ServiceNowConnector:
    return ServiceNowConnector(servicenow_config, mock_mode=True)


@pytest.fixture
def sample_cherwell_record() -> CherwellRecord:
    return CherwellRecord(
        rec_id="abc123def456",
        incident_id="INC-999001",
        short_description="Test: printer offline",
        description="The printer on floor 2 is offline",
        priority="3",
        status="New",
        category="Hardware",
        sub_category="Printer",
        owned_by="Test User",
        owned_by_team="Desktop Support",
        created_date="2024-01-01T10:00:00Z",
        last_modified_date="2024-01-01T10:05:00Z",
        requester="Jane Doe",
        customer="HR",
        service="Print Services",
        impact="3",
        urgency="3",
        raw_data={
            "RecID": "abc123def456",
            "IncidentID": "INC-999001",
            "ShortDescription": "Test: printer offline",
            "Description": "The printer on floor 2 is offline",
            "Priority": "3",
            "Status": "New",
            "Category": "Hardware",
            "SubCategory": "Printer",
            "OwnedBy": "Test User",
            "OwnedByTeam": "Desktop Support",
            "CreatedDateTime": "2024-01-01T10:00:00Z",
            "LastModifiedDateTime": "2024-01-01T10:05:00Z",
            "Requester": "Jane Doe",
            "Customer": "HR",
            "Service": "Print Services",
            "Impact": "3",
            "Urgency": "3",
        },
    )


@pytest.fixture
def sample_schema_mapping() -> SchemaMapping:
    return SchemaMapping(
        field_mappings=[
            FieldMapping(source_field="RecID", target_field="source_rec_id", required=True),
            FieldMapping(source_field="ShortDescription", target_field="short_description", required=True),
            FieldMapping(source_field="Description", target_field="description"),
            FieldMapping(source_field="Priority", target_field="priority", transform="priority_map"),
            FieldMapping(source_field="Status", target_field="state", transform="status_map"),
            FieldMapping(source_field="Category", target_field="category"),
            FieldMapping(source_field="SubCategory", target_field="subcategory"),
            FieldMapping(source_field="OwnedBy", target_field="assigned_to"),
            FieldMapping(source_field="OwnedByTeam", target_field="assignment_group"),
            FieldMapping(source_field="CreatedDateTime", target_field="sys_created_on"),
            FieldMapping(source_field="LastModifiedDateTime", target_field="sys_updated_on"),
            FieldMapping(source_field="Requester", target_field="caller_id"),
            FieldMapping(source_field="Service", target_field="business_service"),
            FieldMapping(source_field="Impact", target_field="impact"),
            FieldMapping(source_field="Urgency", target_field="urgency"),
            FieldMapping(source_field="", target_field="u_migration_source", default_value="cherwell"),
        ]
    )


@pytest.fixture
def sample_migration_state(sample_cherwell_record, sample_schema_mapping) -> MigrationState:
    return MigrationState(
        is_mock_mode=True,
        schema_mapping=sample_schema_mapping,
        records=[MigrationRecord(source_record=sample_cherwell_record)],
        total_records=1,
    )
