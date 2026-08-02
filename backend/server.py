import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from waitress import serve

from app import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"


def configure_logging() -> Path:
    log_directory = Path(
        os.environ.get("DSMS_LOG_DIR", str(DEFAULT_LOG_DIR))
    ).resolve()

    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "dsms-server.log"

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logging.captureWarnings(True)

    return log_path


def main() -> None:
    log_path = configure_logging()

    host = os.environ.get("DSMS_HOST", "127.0.0.1")
    port = int(os.environ.get("DSMS_PORT", "8000"))
    threads = int(os.environ.get("DSMS_THREADS", "8"))

    logger = logging.getLogger("dsms.server")
    logger.info("Starting DSMS at http://%s:%s", host, port)
    logger.info("Server log: %s", log_path)
    logger.info("Waitress threads: %s", threads)

    serve(
        app,
        host=host,
        port=port,
        threads=threads,
    )


if __name__ == "__main__":
    main()