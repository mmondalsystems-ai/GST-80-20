"""Small encrypted secret store for administrator-managed integration keys."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from .config import SECRET_KEY, ANTHROPIC_API_KEY
from .models import AppSecret, User

ANTHROPIC_KEY = "anthropic_api_key"


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def set_secret(db: Session, name: str, value: str, user: User) -> AppSecret:
    value = value.strip()
    row = db.get(AppSecret, name)
    if not row:
        row = AppSecret(name=name, encrypted_value="")
        db.add(row)
    row.encrypted_value = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    row.fingerprint = fingerprint(value)
    row.updated_by_id = user.id
    return row


def get_secret(db: Session, name: str) -> str:
    row = db.get(AppSecret, name)
    if not row:
        return ""
    try:
        return _fernet().decrypt(row.encrypted_value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return ""


def delete_secret(db: Session, name: str) -> bool:
    row = db.get(AppSecret, name)
    if not row:
        return False
    db.delete(row)
    return True


def anthropic_key(db: Session) -> str:
    """A DB-managed key overrides the deployment environment when present."""
    return get_secret(db, ANTHROPIC_KEY) or ANTHROPIC_API_KEY


def anthropic_status(db: Session) -> dict:
    row = db.get(AppSecret, ANTHROPIC_KEY)
    if row and get_secret(db, ANTHROPIC_KEY):
        return {"configured": True, "source": "Admin UI", "fingerprint": row.fingerprint,
                "updated_at": row.updated_at}
    if ANTHROPIC_API_KEY:
        return {"configured": True, "source": "Render/environment variable",
                "fingerprint": fingerprint(ANTHROPIC_API_KEY), "updated_at": None}
    return {"configured": False, "source": "Not configured", "fingerprint": "", "updated_at": None}
