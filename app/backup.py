"""Consistent local-database snapshots after every successful calculation save.

The backup destination may be a client-controlled Google Drive *mirrored*
folder. The active SQLite/PostgreSQL database is never opened from Drive.
"""
import datetime as dt
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import uuid

from .config import BACKUP_DIR, BACKUP_DIR_EXPLICIT, DATABASE_URL, VAR_DIR
from .license import installation_id


def _sqlite_snapshot(destination: Path):
    from .db import engine
    source_name = engine.url.database
    if not source_name or source_name == ":memory:":
        raise RuntimeError("SQLite backup needs an on-disk database")
    source = Path(source_name).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Live database not found at {source}")
    try:
        with closing(sqlite3.connect(source)) as live, closing(sqlite3.connect(destination)) as copy:
            live.backup(copy)
            check = copy.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise RuntimeError("SQLite integrity check failed on the backup")
    except sqlite3.Error as exc:
        raise RuntimeError(f"SQLite database backup failed: {exc}") from exc


def _postgres_snapshot(destination: Path):
    from sqlalchemy.engine import make_url
    url = make_url(DATABASE_URL)
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    command = ["pg_dump", "--format=custom", "--file", str(destination),
               "--host", url.host or "localhost", "--port", str(url.port or 5432),
               "--username", url.username or "", url.database or ""]
    result = subprocess.run(command, env=env, capture_output=True, text=True,
                            timeout=600, check=False)
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("pg_dump failed: " + (result.stderr or "empty backup")[-500:])


def backup_after_run(today=None):
    """Create a distinct complete backup after every committed calculation.

    Returns (created, message). Failure never rolls back an already-saved run;
    the caller must display the failure prominently.
    """
    today = today or dt.date.today()
    if BACKUP_DIR_EXPLICIT and not BACKUP_DIR.is_dir():
        raise FileNotFoundError(f"Configured backup folder is unavailable: {BACKUP_DIR}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    kind = "sqlite3" if DATABASE_URL.startswith("sqlite") else "dump"
    timestamp = dt.datetime.now().strftime("%H%M%S-%f")
    name = f"gst8020-{installation_id()[:8]}-{today:%Y%m%d}-{timestamp}-{uuid.uuid4().hex[:8]}.{kind}"
    final = BACKUP_DIR / name
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gst8020-backup-", dir=VAR_DIR) as folder:
        local_copy = Path(folder) / f"snapshot.{kind}"
        if kind == "sqlite3":
            _sqlite_snapshot(local_copy)
        elif DATABASE_URL.startswith("postgresql"):
            _postgres_snapshot(local_copy)
        else:
            raise RuntimeError("Backup is not configured for this database type")
        staging = BACKUP_DIR / f".backup-{uuid.uuid4().hex[:8]}.partial"
        try:
            shutil.copy2(local_copy, staging)
            if staging.stat().st_size != local_copy.stat().st_size:
                raise RuntimeError("Backup copy size did not match the source snapshot")
            os.replace(staging, final)
        finally:
            staging.unlink(missing_ok=True)
    return True, f"Database backup created at {final}"
