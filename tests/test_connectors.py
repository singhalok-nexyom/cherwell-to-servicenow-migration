"""
Tests for the Cherwell and ServiceNow connectors.
"""

from connectors.cherwell_connector import CherwellConnector  # noqa: F401 - used via fixtures
from connectors.servicenow_connector import ServiceNowConnector  # noqa: F401 - used via fixtures


class TestCherwellConnector:
    def test_authenticate_mock(self, cherwell_connector):
        assert cherwell_connector.authenticate() is True
        assert cherwell_connector._auth_token is not None  # nosec B105

    def test_get_schema_mock(self, cherwell_connector):
        schema = cherwell_connector.get_schema("Incident")
        assert "fields" in schema
        assert len(schema["fields"]) > 0

    def test_get_records_mock(self, cherwell_connector):
        records = cherwell_connector.get_records("Incident", page_size=5)
        assert isinstance(records, list)
        assert len(records) <= 5

    def test_get_records_paging(self, cherwell_connector):
        page0 = cherwell_connector.get_records("Incident", page_size=3, page=0)
        page1 = cherwell_connector.get_records("Incident", page_size=3, page=1)
        # Pages should be different (unless there are fewer than 6 records)
        if len(page0) == 3 and len(page1) > 0:
            assert page0[0]["RecID"] != page1[0]["RecID"]

    def test_get_all_records_returns_all(self, cherwell_connector):
        all_records = cherwell_connector.get_all_records("Incident", page_size=3)
        assert isinstance(all_records, list)
        assert len(all_records) >= 1


class TestServiceNowConnector:
    def test_authenticate_mock(self, servicenow_connector):
        assert servicenow_connector.authenticate() is True
        assert servicenow_connector._auth_token is not None  # nosec B105

    def test_get_schema_mock(self, servicenow_connector):
        schema = servicenow_connector.get_schema("incident")
        assert "fields" in schema
        assert len(schema["fields"]) > 0

    def test_create_record_mock(self, servicenow_connector):
        record = servicenow_connector.create_record("incident", {
            "short_description": "Test incident",
            "source_rec_id": "test123",
        })
        assert "sys_id" in record
        assert "number" in record
        assert record["short_description"] == "Test incident"

    def test_check_duplicate_false(self, servicenow_connector):
        result = servicenow_connector.check_duplicate("incident", "source_rec_id", "nonexistent")
        assert result is False

    def test_check_duplicate_true(self, servicenow_connector):
        servicenow_connector.create_record("incident", {
            "short_description": "Dup check",
            "source_rec_id": "dup-source-id",
        })
        result = servicenow_connector.check_duplicate("incident", "source_rec_id", "dup-source-id")
        assert result is True

    def test_get_records_returns_created(self, servicenow_connector):
        servicenow_connector.create_record("incident", {
            "short_description": "Retrieve me",
            "source_rec_id": "retrieve-test",
        })
        records = servicenow_connector.get_records("incident")
        assert any(r.get("source_rec_id") == "retrieve-test" for r in records)
