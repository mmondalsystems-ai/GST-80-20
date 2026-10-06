"""Small launcher checks, avoiding fragile PowerShell -> Python -c quoting."""
import argparse
from pathlib import Path
import sys

from .config import BACKUP_DIR, BACKUP_DIR_EXPLICIT, DATABASE_URL


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup-dir", "local-db", "license-file", "database-kind"))
    parser.add_argument("value", nargs="?")
    args = parser.parse_args(argv)

    if args.action == "database-kind":
        print("postgresql" if DATABASE_URL.startswith("postgresql") else "sqlite" if DATABASE_URL.startswith("sqlite") else "unsupported")
        return 0

    if args.action == "backup-dir":
        print(BACKUP_DIR if BACKUP_DIR_EXPLICIT else "")
        return 0

    if args.action == "license-file":
        if not args.value:
            print("license-file requires the path to a licence JSON file", file=sys.stderr)
            return 2
        try:
            from .license import installation_id, verify_license_file
            _, _, customer = verify_license_file(Path(args.value), installation_id())
        except Exception:
            print("Licence signature, terms, or installation ID are invalid", file=sys.stderr)
            return 1
        print(customer)
        return 0

    if not DATABASE_URL.startswith("sqlite"):
        print("deploy.bat requires local SQLite; remove the old DATABASE_URL from .env",
              file=sys.stderr)
        return 1
    from .db import engine
    name = engine.url.database
    if not name or name == ":memory:":
        print("deploy.bat requires an on-disk SQLite database", file=sys.stderr)
        return 1
    live = Path(name).resolve()
    backup = BACKUP_DIR.resolve()
    if BACKUP_DIR_EXPLICIT and (live == backup or backup in live.parents):
        print("The live SQLite database cannot be inside the backup folder", file=sys.stderr)
        return 1
    print(live)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
