"""Session authentication, roles and the audit helper."""
import json
import datetime as dt
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
import bcrypt
from sqlalchemy.orm import Session
from .db import get_db
from .models import User, AuditLog

# bcrypt hashes at most 72 bytes of input, so a longer passphrase is hashed to a
# fixed-length digest first rather than being silently truncated.
import hashlib
import base64
import secrets
import hmac
from fastapi import Form


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def require_csrf(request: Request, csrf_token_value: str = Form("")) -> None:
    expected = request.session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected, csrf_token_value):
        raise HTTPException(403, "The form has expired or its security token is missing. Refresh and retry.")


def _prepare(password: str) -> bytes:
    raw = password.encode("utf-8")
    if len(raw) > 72:
        raw = base64.b64encode(hashlib.sha256(raw).digest())
    return raw

ROLES = {
    "admin":    "Administrator — manages masters, users and every module",
    "preparer": "Preparer — uploads files, runs calculations, resolves rectifications",
    "viewer":   "Viewer — read-only access to results and reports",
}


def hash_password(p: str) -> str:
    return bcrypt.hashpw(_prepare(p), bcrypt.gensalt()).decode("utf-8")


def verify_password(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(p), h.encode("utf-8"))
    except Exception:
        return False


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    u = db.get(User, uid)
    return u if (u and u.is_active) else None


class RedirectToLogin(Exception):
    def __init__(self, path: str):
        self.path = path


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    u = current_user(request, db)
    if not u:
        raise RedirectToLogin(request.url.path)
    return u


def require_editor(user: User = Depends(require_user)) -> User:
    if not user.can_edit:
        raise HTTPException(403, "Your account has read-only access. Ask an administrator "
                                 "for the Preparer role to make changes here.")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "This page is restricted to administrators.")
    return user


def audit(db: Session, user: User | None, action: str, entity: str = "", entity_id: str = "",
          before=None, after=None, detail: str = ""):
    """Record a change. Called inside the caller's transaction; caller commits."""
    db.add(AuditLog(
        user_id=user.id if user else None,
        user_label=f"{user.name} <{user.email}>" if user else "system",
        action=action, entity=entity, entity_id=str(entity_id),
        before=json.dumps(before, default=str) if before is not None else "",
        after=json.dumps(after, default=str) if after is not None else "",
        detail=detail,
    ))
