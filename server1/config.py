import os
from pathlib import Path

AGOUTIC_ROOT = Path(os.getenv("AGOUTIC_ROOT", "/media/backup_disk/agoutic_root"))

DB_FOLDER = AGOUTIC_ROOT / "database"
DB_FILE = DB_FOLDER / "agoutic_v23.sqlite"

DB_FOLDER.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{DB_FILE}"
