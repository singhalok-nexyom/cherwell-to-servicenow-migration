"""
ServiceNow REST Table API connector.

In mock_mode (default) the connector simulates API responses locally.
Set MIGRATION_MOCK_MODE=false together with real ServiceNow credentials
to write to a live instance.
"""

import uuid
from typing import Any, Dict, List, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import ServiceNowConfig
from connectors.base_connector import BaseConnector
from utils.logger import get_logger

logger = get_logger(__name__)

MOCK_SN_SCHEMA: Dict[str, Any] = {
    "name": "incident",
    "label": "Incident",
    "fields": [
        {"name": "sys_id", "label": "Sys ID", "type": "GUID", "mandatory": False},
        {"name": "number", "label": "Number", "type": "String", "mandatory": False},
        {"name": "short_description", "label": "Short Description", "type": "String", "mandatory": True},
        {"name": "description", "label": "Description", "type": "String", "mandatory": False},
        {"name": "priority", "label": "Priority", "type": "Integer", "mandatory": False},
        {"name": "state", "label": "State", "type": "Integer", "mandatory": False},
        {"name": "category", "label": "Category", "type": "String", "mandatory": False},
        {"name": "subcategory", "label": "Subcategory", "type": "String", "mandatory": False},
        {"name": "assigned_to", "label": "Assigned To", "type": "Reference", "mandatory": False},
        {"name": "assignment_group", "label": "Assignment Group", "type": "Reference", "mandatory": False},
        {"name": "sys_created_on", "label": "Created", "type": "DateTime", "mandatory": False},
        {"name": "sys_updated_on", "label": "Updated", "type": "DateTime", "mandatory": False},
        {"name": "resolved_at", "label": "Resolved At", "type": "DateTime", "mandatory": False},
        {"name": "closed_at", "label": "Closed At", "type": "DateTime", "mandatory": False},
        {"name": "caller_id", "label": "Caller", "type": "Reference", "mandatory": False},
        {"name": "business_service", "label": "Business Service", "type": "Reference", "mandatory": False},
        {"name": "impact", "label": "Impact", "type": "Integer", "mandatory": False},
        {"name": "urgency", "label": "Urgency", "type": "Integer", "mandatory": False},
        {"name": "source_rec_id", "label": "Source Record ID", "type": "String", "mandatory": False},
        {"name": "u_migration_source", "label": "Migration Source", "type": "String", "mandatory": False},
    ],
}


class ServiceNowConnector(BaseConnector):
    """REST API connector for the ServiceNow Table API."""

    def __init__(self, config: ServiceNowConfig, mock_mode: bool = True) -> None:
        super().__init__(mock_mode=mock_mode)
        self.config = config
        self._session = requests.Session()
        # In-memory mock store so dry-run and live modes share the same class
        self._mock_store: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        if self.mock_mode:
            logger.info("[mock] ServiceNow authentication skipped in mock mode")
            self._auth_token = "MOCK_TOKEN_NOT_A_REAL_CREDENTIAL"  # nosec B105 – placeholder for mock mode only
            return True

        # Validate credentials via a lightweight GET against the instance
        self._session.auth = (self.config.username, self.config.password)
        self._session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        try:
            url = f"{self.config.instance_url}/api/now/{self.config.api_version}/table/sys_user?sysparm_limit=1"
            resp = self._session.get(url, timeout=self.config.timeout, verify=self.config.verify_ssl)
            resp.raise_for_status()
            logger.info("ServiceNow authentication successful")
            return True
        except requests.RequestException as exc:
            logger.error("ServiceNow authentication failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Schema retrieval
    # ------------------------------------------------------------------

    def get_schema(self, object_type: str = "incident") -> Dict[str, Any]:
        if self.mock_mode:
            logger.info("[mock] Returning mock ServiceNow schema for '%s'", object_type)
            return MOCK_SN_SCHEMA

        url = f"{self.config.instance_url}/api/now/{self.config.api_version}/table/{object_type}?sysparm_limit=1"
        try:
            resp = self._session.get(url, timeout=self.config.timeout, verify=self.config.verify_ssl)
            resp.raise_for_status()
            return {"name": object_type, "sample": resp.json()}
        except requests.RequestException as exc:
            logger.error("Failed to retrieve ServiceNow schema: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Record operations
    # ------------------------------------------------------------------

    def get_records(
        self,
        object_type: str = "incident",
        filters: Optional[Dict[str, Any]] = None,
        page_size: int = 50,
        page: int = 0,
    ) -> List[Dict[str, Any]]:
        if self.mock_mode:
            return list(self._mock_store.values())

        params: Dict[str, Any] = {
            "sysparm_limit": page_size,
            "sysparm_offset": page * page_size,
        }
        if filters:
            params["sysparm_query"] = self._build_query(filters)

        url = f"{self.config.instance_url}/api/now/{self.config.api_version}/table/{object_type}"
        resp = self._get(url, params=params)
        return resp.get("result", [])

    def create_record(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a record in ServiceNow. Returns the created record dict."""
        if self.mock_mode:
            sys_id = str(uuid.uuid4()).replace("-", "")
            data["sys_id"] = sys_id
            data["number"] = f"INC{len(self._mock_store) + 1000001:07d}"
            self._mock_store[sys_id] = data
            logger.debug("[mock] Created SN record %s (%s)", data["number"], sys_id)
            return data

        url = f"{self.config.instance_url}/api/now/{self.config.api_version}/table/{table}"
        resp = self._post(url, data)
        return resp.get("result", {})

    def check_duplicate(self, table: str, field: str, value: str) -> bool:
        """Return True if a record with field=value already exists."""
        if self.mock_mode:
            return any(r.get(field) == value for r in self._mock_store.values())

        params = {"sysparm_query": f"{field}={value}", "sysparm_limit": 1}
        url = f"{self.config.instance_url}/api/now/{self.config.api_version}/table/{table}"
        resp = self._get(url, params=params)
        return bool(resp.get("result"))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_query(filters: Dict[str, Any]) -> str:
        return "^".join(f"{k}={v}" for k, v in filters.items())

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        resp = self._session.get(
            url,
            params=params,
            timeout=self.config.timeout,
            verify=self.config.verify_ssl,
        )
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _post(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = self._session.post(
            url,
            json=payload,
            timeout=self.config.timeout,
            verify=self.config.verify_ssl,
        )
        resp.raise_for_status()
        return resp.json()
