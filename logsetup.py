"""Shared logging setup for all CLI tools (convert.py, deploy.py, combine.py,
appearance.py, sim_deploy.py, dump.py, gui.py).

Every run writes a timestamped, detailed log file to logs/, in addition to a
readable summary printed to the console. If something goes wrong on the
device after a restore, the log file for that run has the full detail
(paths, fileIDs, generated UUIDs, SQL statements, tracebacks) to figure out
what was actually written.
"""

import logging
import platform
import sys
import time
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOGGER_NAME = "tendies"


def _force_utf8_console():
    """On Windows, the console can be configured with an encoding (cp1252,
    cp850...) that doesn't support the accented characters used in some
    messages. Force UTF-8 to avoid crashing on the first accented letter."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def setup_logging(run_name: str, argv=None) -> Path:
    _force_utf8_console()
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"{run_name}-{timestamp}.log"

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-7s [%(funcName)s] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    ))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.debug(f"===== new run: {run_name} =====")
    logger.debug(f"python={sys.version.split()[0]} platform={platform.platform()}")
    logger.debug(f"argv={argv if argv is not None else sys.argv}")
    logger.info(f"Detailed log for this run: {log_path}")

    return log_path


def get_logger():
    return logging.getLogger(LOGGER_NAME)
