"""Explicit, first-use SQLite recovery from local and configured backups."""
from contextlib import closing
import datetime as dt
import os
from pathlib import Path
import sqlite3
import uuid

from .config import BACKUP_DIR, VAR_DIR


def connect_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def validate_backup(path):
    from .models import Base
    with closing(connect_readonly(path)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup integrity check failed")
        for table in Base.metadata.sorted_tables:
            columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table.name}")')}
            if not {column.name for column in table.columns}.issubset(columns):
                raise ValueError("Backup schema is incompatible with this application")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Backup contains broken relationships")
        return db.execute("SELECT count(*) FROM runs").fetchone()[0]


def discover_backups(folders):
    found = []
    seen = set()
    for folder in folders:
        try:
            for path in Path(folder).glob("gst8020-*.sqlite3"):
                resolved = path.resolve()
                if resolved not in seen and resolved.is_file():
                    seen.add(resolved)
                    found.append(resolved)
        except OSError as exc:
            print(f"Backup folder unavailable: {folder} ({exc})")
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def restore_backup(source, live, recovery_dir):
    source, live = Path(source).resolve(), Path(live).resolve()
    if source == live:
        raise ValueError("The live database is not a recovery source")
    validate_backup(source)
    # Restore only before any calculation exists. Acquire an exclusive SQLite
    # transaction to reject another active writer, then retain a consistent copy.
    with closing(sqlite3.connect(live, timeout=1)) as db:
        db.execute("BEGIN EXCLUSIVE")
        if db.execute("SELECT count(*) FROM runs").fetchone()[0]:
            raise ValueError("Existing calculations cannot be overwritten by initialization")
        activations = db.execute("SELECT license_id, installation_id, activated_at, last_seen_at FROM license_activations").fetchall()
        db.rollback()
    if any(Path(str(live) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Close all application processes before restoring the database")
    recovery_dir = Path(recovery_dir)
    recovery_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    saved = recovery_dir / f"before-{dt.datetime.now():%Y%m%d-%H%M%S}-{token}.sqlite3"
    staged = live.parent / f".restore-{token}.sqlite3"
    try:
        with closing(connect_readonly(live)) as original, closing(sqlite3.connect(saved)) as copy:
            original.backup(copy)
        with closing(connect_readonly(source)) as original, closing(sqlite3.connect(staged)) as copy:
            original.backup(copy)
            # A stale backup must not restart an already activated local licence.
            for license_id, installation, activated, last_seen in activations:
                old = copy.execute("SELECT activated_at, last_seen_at FROM license_activations WHERE license_id=?", (license_id,)).fetchone()
                if old:
                    from .license import as_utc
                    date_key = lambda value: as_utc(dt.datetime.fromisoformat(value))
                    activated = min(activated, old[0], key=date_key)
                    last_seen = max(last_seen, old[1], key=date_key)
                copy.execute("INSERT OR REPLACE INTO license_activations VALUES (?, ?, ?, ?)",
                             (license_id, installation, activated, last_seen))
            copy.commit()
        validate_backup(staged)
        with closing(sqlite3.connect(live, timeout=1)) as current:
            current.execute("BEGIN EXCLUSIVE")
            if current.execute("SELECT count(*) FROM runs").fetchone()[0]:
                raise ValueError("A calculation was saved during recovery; current data retained")
            current.rollback()
        os.replace(staged, live)
    finally:
        staged.unlink(missing_ok=True)
    return saved


def main():
    from .db import engine
    if engine.dialect.name != "sqlite":
        return 0
    live = Path(engine.url.database).resolve()
    engine.dispose()
    with closing(connect_readonly(live)) as db:
        if db.execute("SELECT count(*) FROM runs").fetchone()[0]:
            print("Existing calculations found. Current database retained; recovery skipped.")
            return 0
    folders = list(dict.fromkeys([VAR_DIR / "backups", BACKUP_DIR]))
    print("Checking backup folders:")
    for folder in folders:
        print(f"  {folder}")
    backups = discover_backups(folders)
    if not backups:
        print("No previous SQLite backups found. Continuing with the current database.")
        return 0
    eligible = []
    for path in backups:
        try:
            count = validate_backup(path)
            if count:
                eligible.append(path)
                print(f"{len(eligible)}. {path} ({count} calculations)")
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f"Ignoring invalid/incompatible backup: {path} ({exc})")
    if not eligible:
        return 0
    try:
        choice = input("Backup number to restore, or Enter to keep current database: ").strip()
        if not choice:
            return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(eligible):
            print("Invalid selection. Nothing was restored.")
            return 0
        source = eligible[int(choice) - 1]
        print("Recovery restores ALL accounts/passwords and data from this backup.")
        print("Use its original login credentials. Installation ID and licence file remain unchanged.")
        print("Close any other running copies of this application first.")
        if input("Type RESTORE to confirm: ").strip() != "RESTORE":
            print("Recovery cancelled.")
            return 0
        saved = restore_backup(source, live, VAR_DIR / "recovery")
        password_file = VAR_DIR / "first-admin-password.txt"
        if password_file.exists():
            password_file.rename(VAR_DIR / f"first-admin-password.pre-restore-{uuid.uuid4().hex[:8]}.txt")
        print(f"Recovery complete. Previous database retained at {saved}")
    except (EOFError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"Recovery stopped: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
