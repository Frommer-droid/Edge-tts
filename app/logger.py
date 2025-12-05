"""Application-wide logger setup."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

_LOGGERS: dict[str, logging.Logger] = {}


def get_logger(name: str, path: Path) -> logging.Logger:
    """Return a configured logger that writes to `path`. File is truncated each run."""
    if name in _LOGGERS:
        return _LOGGERS[name]

    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    _LOGGERS[name] = logger
    logger.debug("Logger initialised at %s", path)
    return logger
