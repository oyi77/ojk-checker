"""Logging configuration — safe, minimal, service-ready."""

import logging
import sys
from typing import Optional

_logger = logging.getLogger("slik_checker")


def setup_logging(
    level: Optional[str] = None,
    output_format: Optional[str] = None,
) -> None:
    fmt = output_format or "console"
    log_level = getattr(logging, (level or "INFO").upper(), logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    if fmt == "json":
        formatter = logging.Formatter(
            '{"ts": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "msg": %(message)s}',
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    handler.setFormatter(formatter)
    root.addHandler(handler)

    for name in ["urllib3", "selenium", "apscheduler", "PIL", "easyocr", "ddddocr"]:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    if not logging.getLogger().handlers:
        setup_logging()
    return logging.getLogger(name)
