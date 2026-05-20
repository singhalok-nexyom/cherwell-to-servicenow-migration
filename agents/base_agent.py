"""Abstract base agent shared by all pipeline agents."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from models.data_models import MigrationState
from utils.logger import get_logger


@dataclass
class AgentResult:
    success: bool
    state: MigrationState
    message: str = ""
    error: Optional[str] = None


class BaseAgent(ABC):
    """Every pipeline agent must inherit this class and implement `run()`."""

    def __init__(self, name: str, log_level: str = "INFO") -> None:
        self.name = name
        self.logger = get_logger(name, log_level)

    @abstractmethod
    def run(self, state: MigrationState) -> AgentResult:
        """Execute the agent logic and return an updated AgentResult."""

    def _info(self, msg: str, *args: Any) -> None:
        self.logger.info(msg, *args)

    def _warn(self, msg: str, *args: Any) -> None:
        self.logger.warning(msg, *args)

    def _error(self, msg: str, *args: Any) -> None:
        self.logger.error(msg, *args)
