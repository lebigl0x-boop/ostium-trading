import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


def setup_logging(level: str = "INFO", log_dir: str | Path = "logs", log_name: str = "bot.log") -> None:
    """
    Configure console + rotating file logging.
    Safe to call multiple times; handlers are reset.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(level.upper())

    # Clear existing handlers to avoid duplicate logs when reloading.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        log_path / log_name,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name)


