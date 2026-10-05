"""Logging configuration."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import LoggingConfig

_COLORS = {
    "DEBUG": "\033[38;5;245m",
    "INFO": "\033[38;5;39m",
    "WARNING": "\033[38;5;214m",
    "ERROR": "\033[38;5;203m",
    "CRITICAL": "\033[48;5;196m\033[38;5;231m",
}
_RESET = "\033[0m"


class _ColourFormatter(logging.Formatter):
    def __init__(self, use_colour: bool) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)-22s %(message)s",
                         datefmt="%H:%M:%S")
        self.use_colour = use_colour

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if not self.use_colour:
            return text
        colour = _COLORS.get(record.levelname, "")
        return f"{colour}{text}{_RESET}" if colour else text


def setup_logging(cfg: LoggingConfig) -> None:
    """Install console (and optionally file) handlers."""
    level = getattr(logging, (cfg.level or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(_ColourFormatter(sys.stdout.isatty()))
    root.addHandler(console)

    if cfg.to_file:
        directory = Path(cfg.dir)
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(directory / "omega.log", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(name)s %(message)s"
            )
        )
        root.addHandler(file_handler)

    # third-party noise
    for noisy in ("uvicorn.access", "urllib3", "asyncio", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
