from __future__ import annotations

import logging
from pathlib import Path

LOG_PATH = Path.home() / "AppData" / "Roaming" / "WinDrop" / "windrop.log"


def get_logger() -> logging.Logger:
    logger = logging.getLogger("windrop")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
