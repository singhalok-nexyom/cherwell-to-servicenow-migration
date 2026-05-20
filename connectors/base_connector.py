"""Abstract base class shared by all API connectors."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseConnector(ABC):
    """Common interface every connector must implement."""

    def __init__(self, mock_mode: bool = True) -> None:
        self.mock_mode = mock_mode
        self._auth_token: Optional[str] = None

    @abstractmethod
    def authenticate(self) -> bool:
        """Establish / refresh authentication. Returns True on success."""

    @abstractmethod
    def get_records(
        self,
        object_type: str,
        filters: Optional[Dict[str, Any]] = None,
        page_size: int = 50,
        page: int = 0,
    ) -> List[Dict[str, Any]]:
        """Fetch a page of records from the source system."""

    @abstractmethod
    def get_schema(self, object_type: str) -> Dict[str, Any]:
        """Return the field schema for the given object / table type."""

    def is_authenticated(self) -> bool:
        return self._auth_token is not None or self.mock_mode
