"""Small shared helpers: UTC timestamps and module loggers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"sentinel.{name}")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger
