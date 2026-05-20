"""Structured logging with Rich console output."""

import logging
import sys
from rich.logging import RichHandler
from rich.console import Console

console = Console()


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Return a Rich-formatted logger for the given module name."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )
    logger = logging.getLogger(name)
    # Prevent duplicate handlers if called multiple times
    if not logger.handlers:
        logger.addHandler(RichHandler(console=console, rich_tracebacks=True))
    return logger
