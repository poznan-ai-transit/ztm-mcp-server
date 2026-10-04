import logging
from pathlib import Path

LOGS_DIRECTORY: Path = Path(__file__).resolve().parent.parent / "_logs_"
logger: logging.Logger = logging.getLogger("server")


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the shared application logger once, without duplicate handlers."""
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return

    LOGS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOGS_DIRECTORY / "server.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(module)s.%(funcName)s] %(message)s")
    )
    logger.addHandler(handler)
