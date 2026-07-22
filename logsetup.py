"""Configuration de logs partagée par convert.py et deploy.py.

Chaque exécution écrit un fichier journal horodaté et complet dans logs/,
en plus d'un résumé lisible affiché dans la console. En cas de problème sur
l'appareil après une restauration, le fichier journal correspondant à cette
exécution contient tout le détail (chemins, fileID, UUID générés, requêtes
SQL, tracebacks) pour comprendre ce qui a été écrit.
"""

import logging
import platform
import sys
import time
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOGGER_NAME = "tendies"


def _force_utf8_console():
    """Sur Windows, la console peut être configurée dans un encodage (cp1252,
    cp850...) qui ne supporte pas les accents utilisés dans les messages. On
    force l'UTF-8 pour éviter un crash à la première lettre accentuée."""
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

    logger.debug(f"===== nouvelle exécution : {run_name} =====")
    logger.debug(f"python={sys.version.split()[0]} platform={platform.platform()}")
    logger.debug(f"argv={argv if argv is not None else sys.argv}")
    logger.info(f"Journal détaillé de cette exécution : {log_path}")

    return log_path


def get_logger():
    return logging.getLogger(LOGGER_NAME)
