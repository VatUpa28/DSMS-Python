import os
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BACKEND_DIR / "database" / "app.db"

DB_PATH = os.environ.get(
    "DSMS_DB_PATH",
    str(DEFAULT_DB_PATH),
)