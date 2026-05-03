"""Centralized logging configuration for WOS Bot."""

import logging
import os
from logging.handlers import RotatingFileHandler

_configured = False


def setup_logging():
    """Configure root logger with console and file handlers."""
    global _configured
    if _configured:
        return
    _configured = True

    os.makedirs("log", exist_ok=True)

    root = logging.getLogger("wosbot")
    root.setLevel(logging.DEBUG)

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console.setFormatter(console_fmt)
    root.addHandler(console)

    # Rotating file handler
    file_handler = RotatingFileHandler(
        "log/bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger under the wosbot namespace."""
    setup_logging()
    return logging.getLogger(f"wosbot.{name}")
