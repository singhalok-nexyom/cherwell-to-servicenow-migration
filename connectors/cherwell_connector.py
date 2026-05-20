"""
Cherwell REST API connector.

In mock_mode (default) the connector returns pre-built synthetic records
so the pipeline can be exercised without live credentials.
Set MIGRATION_MOCK_MODE=false to talk to a real Cherwell server.
"""

from typing import Any, Dict, List, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import CherwellConfig
from connectors.base_connector import BaseConnector
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Synthetic data used in mock mode
# ---------------------------------------------------------------------------

MOCK_INCIDENTS: List[Dict[str, Any]] = [
    {
        "RecID": "a1b2c3d4e5f601234567890abcdef01",
        "IncidentID": "INC-100001",
        "ShortDescription": "Email service outage affecting all users",
        "Description": "Users cannot send or receive emails. Exchange server is unresponsive since 08:45 AM.",
        "Priority": "1",
        "Status": "In Progress",
        "Category": "Email",
        "SubCategory": "Exchange",
        "OwnedBy": "Alice Johnson",
        "OwnedByTeam": "Infrastructure",
        "CreatedDateTime": "2024-03-01T08:50:00Z",
        "LastModifiedDateTime": "2024-03-01T09:30:00Z",
        "ResolvedDateTime": None,
        "ClosedDateTime": None,
        "Requester": "Bob Smith",
        "Customer": "Finance Department",
        "Service": "Email Services",
        "Impact": "1",
        "Urgency": "1",
    },
    {
        "RecID": "b2c3d4e5f6a712345678901bcdef012",
        "IncidentID": "INC-100002",
        "ShortDescription": "VPN connection failing for remote staff",
        "Description": "Remote workers unable to connect via VPN. Error code 800.",
        "Priority": "2",
        "Status": "In Progress",
        "Category": "Network",
        "SubCategory": "VPN",
        "OwnedBy": "Carol Davis",
        "OwnedByTeam": "Network",
        "CreatedDateTime": "2024-03-01T09:15:00Z",
        "LastModifiedDateTime": "2024-03-01T10:00:00Z",
        "ResolvedDateTime": None,
        "ClosedDateTime": None,
        "Requester": "Dave Wilson",
        "Customer": "Sales Department",
        "Service": "Remote Access",
        "Impact": "2",
        "Urgency": "2",
    },
    {
        "RecID": "c3d4e5f6a7b823456789012cdef0123",
        "IncidentID": "INC-100003",
        "ShortDescription": "Printer on Floor 3 not printing",
        "Description": "HP LaserJet on Floor 3 shows offline. Restart did not resolve.",
        "Priority": "3",
        "Status": "New",
        "Category": "Hardware",
        "SubCategory": "Printer",
        "OwnedBy": "Eve Martinez",
        "OwnedByTeam": "Desktop Support",
        "CreatedDateTime": "2024-03-01T10:00:00Z",
        "LastModifiedDateTime": "2024-03-01T10:05:00Z",
        "ResolvedDateTime": None,
        "ClosedDateTime": None,
        "Requester": "Frank Lee",
        "Customer": "HR Department",
        "Service": "Print Services",
        "Impact": "3",
        "Urgency": "3",
    },
    {
        "RecID": "d4e5f6a7b8c934567890123def01234",
        "IncidentID": "INC-100004",
        "ShortDescription": "Laptop screen flickering intermittently",
        "Description": "Dell XPS 15 screen flickers every few minutes. Affects user productivity.",
        "Priority": "3",
        "Status": "Pending",
        "Category": "Hardware",
        "SubCategory": "Laptop",
        "OwnedBy": "Grace Kim",
        "OwnedByTeam": "Desktop Support",
        "CreatedDateTime": "2024-03-01T11:00:00Z",
        "LastModifiedDateTime": "2024-03-01T11:20:00Z",
        "ResolvedDateTime": None,
        "ClosedDateTime": None,
        "Requester": "Hank Brown",
        "Customer": "Engineering",
        "Service": "End User Computing",
        "Impact": "3",
        "Urgency": "3",
    },
    {
        "RecID": "e5f6a7b8c9d045678901234ef012345",
        "IncidentID": "INC-100005",
        "ShortDescription": "SharePoint permissions denied for new team",
        "Description": "New product team cannot access SharePoint project site. Error 403.",
        "Priority": "2",
        "Status": "In Progress",
        "Category": "Applications",
        "SubCategory": "SharePoint",
        "OwnedBy": "Ivan Chen",
        "OwnedByTeam": "Collaboration",
        "CreatedDateTime": "2024-03-01T11:30:00Z",
        "LastModifiedDateTime": "2024-03-01T12:00:00Z",
        "ResolvedDateTime": None,
        "ClosedDateTime": None,
        "Requester": "Julia White",
        "Customer": "Product Department",
        "Service": "Collaboration Tools",
        "Impact": "2",
        "Urgency": "2",
    },
    {
        "RecID": "f6a7b8c9d0e156789012345f0123456",
        "IncidentID": "INC-100006",
        "ShortDescription": "Database backup job failed overnight",
        "Description": "Nightly SQL Server backup job failed with I/O error. Backup incomplete.",
        "Priority": "1",
        "Status": "In Progress",
        "Category": "Database",
        "SubCategory": "SQL Server",
        "OwnedBy": "Kevin Zhang",
        "OwnedByTeam": "Database Administration",
        "CreatedDateTime": "2024-03-01T06:00:00Z",
        "LastModifiedDateTime": "2024-03-01T07:30:00Z",
        "ResolvedDateTime": None,
        "ClosedDateTime": None,
        "Requester": "Laura Adams",
        "Customer": "IT Operations",
        "Service": "Database Services",
        "Impact": "1",
        "Urgency": "1",
    },
    {
        "RecID": "a7b8c9d0e1f267890123456012345678",
        "IncidentID": "INC-100007",
        "ShortDescription": "Slow response on ERP system",
        "Description": "SAP ERP is running unusually slow. Page loads take 30+ seconds.",
        "Priority": "2",
        "Status": "In Progress",
        "Category": "Applications",
        "SubCategory": "ERP",
        "OwnedBy": "Mike Turner",
        "OwnedByTeam": "Application Support",
        "CreatedDateTime": "2024-03-01T13:00:00Z",
        "LastModifiedDateTime": "2024-03-01T13:45:00Z",
        "ResolvedDateTime": None,
        "ClosedDateTime": None,
        "Requester": "Nancy Hill",
        "Customer": "Operations",
        "Service": "ERP Services",
        "Impact": "2",
        "Urgency": "2",
    },
    {
        "RecID": "b8c9d0e1f2a378901234567123456789",
        "IncidentID": "INC-100008",
        "ShortDescription": "New employee onboarding account not created",
        "Description": "New joiner starting Monday has no AD account or email provisioned.",
        "Priority": "2",
        "Status": "New",
        "Category": "Access Management",
        "SubCategory": "Active Directory",
        "OwnedBy": "Oscar Green",
        "OwnedByTeam": "Identity & Access",
        "CreatedDateTime": "2024-03-01T14:00:00Z",
        "LastModifiedDateTime": "2024-03-01T14:00:00Z",
        "ResolvedDateTime": None,
        "ClosedDateTime": None,
        "Requester": "Patrice Moore",
        "Customer": "HR Department",
        "Service": "Identity Services",
        "Impact": "3",
        "Urgency": "2",
    },
    {
        "RecID": "c9d0e1f2a3b489012345678234567890",
        "IncidentID": "INC-100009",
        "ShortDescription": "Wi-Fi drops in conference room B",
        "Description": "Wireless signal drops every 10-15 minutes in Conference Room B on 2nd floor.",
        "Priority": "3",
        "Status": "Resolved",
        "Category": "Network",
        "SubCategory": "Wireless",
        "OwnedBy": "Quinn Roberts",
        "OwnedByTeam": "Network",
        "CreatedDateTime": "2024-02-28T15:00:00Z",
        "LastModifiedDateTime": "2024-03-01T11:00:00Z",
        "ResolvedDateTime": "2024-03-01T11:00:00Z",
        "ClosedDateTime": None,
        "Requester": "Rachel Scott",
        "Customer": "All Staff",
        "Service": "Network Services",
        "Impact": "3",
        "Urgency": "3",
    },
    {
        "RecID": "d0e1f2a3b4c590123456789345678901",
        "IncidentID": "INC-100010",
        "ShortDescription": "SSL certificate expired on customer portal",
        "Description": "Production customer portal shows certificate expired warning. Urgent fix required.",
        "Priority": "1",
        "Status": "In Progress",
        "Category": "Security",
        "SubCategory": "Certificates",
        "OwnedBy": "Sam Parker",
        "OwnedByTeam": "Security",
        "CreatedDateTime": "2024-03-01T07:00:00Z",
        "LastModifiedDateTime": "2024-03-01T07:30:00Z",
        "ResolvedDateTime": None,
        "ClosedDateTime": None,
        "Requester": "Tina Evans",
        "Customer": "Customer Services",
        "Service": "Web Services",
        "Impact": "1",
        "Urgency": "1",
    },
]

MOCK_SCHEMA: Dict[str, Any] = {
    "businessObjectId": "Incident",
    "displayName": "Incident",
    "fields": [
        {"fieldId": "RecID", "displayName": "Record ID", "fieldType": "Text", "required": True},
        {"fieldId": "IncidentID", "displayName": "Incident ID", "fieldType": "Text", "required": True},
        {"fieldId": "ShortDescription", "displayName": "Short Description", "fieldType": "Text", "required": True},
        {"fieldId": "Description", "displayName": "Description", "fieldType": "MemoField", "required": False},
        {"fieldId": "Priority", "displayName": "Priority", "fieldType": "Number", "required": True},
        {"fieldId": "Status", "displayName": "Status", "fieldType": "Text", "required": True},
        {"fieldId": "Category", "displayName": "Category", "fieldType": "Text", "required": False},
        {"fieldId": "SubCategory", "displayName": "Sub Category", "fieldType": "Text", "required": False},
        {"fieldId": "OwnedBy", "displayName": "Owned By", "fieldType": "Text", "required": False},
        {"fieldId": "OwnedByTeam", "displayName": "Owned By Team", "fieldType": "Text", "required": False},
        {"fieldId": "CreatedDateTime", "displayName": "Created Date Time", "fieldType": "DateTime", "required": True},
        {"fieldId": "LastModifiedDateTime", "displayName": "Last Modified", "fieldType": "DateTime", "required": False},
        {"fieldId": "ResolvedDateTime", "displayName": "Resolved Date Time", "fieldType": "DateTime", "required": False},
        {"fieldId": "ClosedDateTime", "displayName": "Closed Date Time", "fieldType": "DateTime", "required": False},
        {"fieldId": "Requester", "displayName": "Requester", "fieldType": "Text", "required": False},
        {"fieldId": "Customer", "displayName": "Customer", "fieldType": "Text", "required": False},
        {"fieldId": "Service", "displayName": "Service", "fieldType": "Text", "required": False},
        {"fieldId": "Impact", "displayName": "Impact", "fieldType": "Number", "required": False},
        {"fieldId": "Urgency", "displayName": "Urgency", "fieldType": "Number", "required": False},
    ],
}


class CherwellConnector(BaseConnector):
    """REST API connector for the Cherwell ITSM platform."""

    def __init__(self, config: CherwellConfig, mock_mode: bool = True) -> None:
        super().__init__(mock_mode=mock_mode)
        self.config = config
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        if self.mock_mode:
            logger.info("[mock] Cherwell authentication skipped in mock mode")
            self._auth_token = "MOCK_TOKEN_NOT_A_REAL_CREDENTIAL"  # nosec B105 – placeholder for mock mode only
            return True

        url = f"{self.config.base_url}/token"
        payload = {
            "grant_type": self.config.grant_type,
            "client_id": self.config.client_id,
            "username": self.config.username,
            "password": self.config.password,
        }
        try:
            resp = self._session.post(
                url,
                data=payload,
                timeout=self.config.timeout,
                verify=self.config.verify_ssl,
            )
            resp.raise_for_status()
            token_data = resp.json()
            self._auth_token = token_data.get("access_token")
            self._session.headers.update({"Authorization": f"Bearer {self._auth_token}"})
            logger.info("Cherwell authentication successful")
            return True
        except requests.RequestException as exc:
            logger.error("Cherwell authentication failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Schema retrieval
    # ------------------------------------------------------------------

    def get_schema(self, object_type: str = "Incident") -> Dict[str, Any]:
        if self.mock_mode:
            logger.info("[mock] Returning mock Cherwell schema for '%s'", object_type)
            return MOCK_SCHEMA

        url = f"{self.config.base_url}/api/V1/getbusinessobjectschema/busobname/{object_type}"
        resp = self._get(url)
        return resp

    # ------------------------------------------------------------------
    # Record retrieval
    # ------------------------------------------------------------------

    def get_records(
        self,
        object_type: str = "Incident",
        filters: Optional[Dict[str, Any]] = None,
        page_size: int = 50,
        page: int = 0,
    ) -> List[Dict[str, Any]]:
        if self.mock_mode:
            start = page * page_size
            end = start + page_size
            batch = MOCK_INCIDENTS[start:end]
            logger.info(
                "[mock] Returning %d Cherwell records (page %d)", len(batch), page
            )
            return batch

        url = f"{self.config.base_url}/api/V1/getsearchresults"
        payload = {
            "busObName": object_type,
            "filters": filters or [],
            "pageSize": page_size,
            "pageNumber": page,
        }
        resp = self._post(url, payload)
        return resp.get("businessObjects", [])

    def get_all_records(
        self,
        object_type: str = "Incident",
        filters: Optional[Dict[str, Any]] = None,
        page_size: int = 50,
    ) -> List[Dict[str, Any]]:
        """Fetch all records across pages."""
        all_records: List[Dict[str, Any]] = []
        page = 0
        while True:
            batch = self.get_records(object_type, filters, page_size, page)
            if not batch:
                break
            all_records.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        logger.info("Fetched %d total records from Cherwell", len(all_records))
        return all_records

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _get(self, url: str) -> Dict[str, Any]:
        resp = self._session.get(url, timeout=self.config.timeout, verify=self.config.verify_ssl)
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
