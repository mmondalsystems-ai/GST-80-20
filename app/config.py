"""Application settings.

Read from the environment, with a `.env` file in the application directory as
the usual source. Everything has a working default, so the application starts
and serves on a machine where nothing has been configured yet -- the installer
writes a proper `.env`, but a missing one is not a failure.
"""
import os
import secrets
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:                                   # dotenv is optional
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("GST8020_DATA_DIR", str(
    Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GST-80-20"
    if getattr(sys, "frozen", False) else BASE_DIR)))

if load_dotenv:
    load_dotenv(DATA_DIR / ".env")

VAR_DIR = Path(os.getenv("VAR_DIR", DATA_DIR / "var"))


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


VAR_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    """The key that signs session cookies.

    Taken from the environment when set. Otherwise one is generated and kept in
    `var/secret.key` so sessions survive a restart -- without this, a server
    started before anyone edited `.env` would sign everybody out on every
    restart, which looks like a broken login rather than a missing setting.
    """
    env = os.getenv("SECRET_KEY", "").strip()
    if env and env not in ("CHANGE_ME_TO_A_LONG_RANDOM_STRING", "change-me"):
        return env
    key_file = VAR_DIR / "secret.key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(48)
    key_file.write_text(key, encoding="utf-8")
    try:
        key_file.chmod(0o600)
    except OSError:                                   # Windows, or a odd filesystem
        pass
    return key


def _database_url() -> str:
    """PostgreSQL when configured, otherwise a local SQLite file.

    SQLite keeps a first install working with no database server at all. It is
    fine for a pilot or a single preparer; PostgreSQL is the right choice once
    several people use the portal at once. Moving between them is a
    configuration change, not a code change.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if url and "CHANGE_ME" not in url:
        return url
    return f"sqlite:///{(VAR_DIR / 'finops.db').as_posix()}"


def _bootstrap_password() -> str:
    """The first administrator's password.

    New installations use the agreed temporary password. Existing saved or
    explicitly configured bootstrap passwords remain unchanged. Change the
    temporary password immediately after signing in.
    """
    env = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    if env and "CHANGE" not in env.upper():
        return env
    pw_file = VAR_DIR / "first-admin-password.txt"
    if pw_file.exists():
        return pw_file.read_text(encoding="utf-8").strip()
    pw = "admin123456789"
    pw_file.write_text(pw, encoding="utf-8")
    try:
        pw_file.chmod(0o600)
    except OSError:
        pass
    return pw


DATABASE_URL = _database_url()
SECRET_KEY = _secret_key()
SESSION_HTTPS_ONLY = _bool("SESSION_HTTPS_ONLY", False)
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", VAR_DIR / "uploads"))
# Optional existing client-controlled, mirrored Google Drive backup folder.
# The live database remains on this PC's local disk.
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", VAR_DIR / "backups"))
BACKUP_DIR_EXPLICIT = bool(os.getenv("BACKUP_DIR", "").strip())
BOOTSTRAP_ADMIN_EMAIL = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@oswalgroup.net").strip().lower()
BOOTSTRAP_ADMIN_PASSWORD = _bootstrap_password()

ORG_NAME = os.getenv("ORG_NAME", "Oswal Group")
PORTAL_NAME = os.getenv("PORTAL_NAME", "Finance Operations Portal")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

USING_SQLITE = DATABASE_URL.startswith("sqlite")
GENERATED_PASSWORD_FILE = VAR_DIR / "first-admin-password.txt"
