"""Finance Operations Portal — application entry point."""
import json
import datetime as dt
import threading
import time
from collections import defaultdict
from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select, func, text as sql_text
from sqlalchemy.exc import SQLAlchemyError

from .config import (SECRET_KEY, SESSION_HTTPS_ONLY, BASE_DIR, ORG_NAME, PORTAL_NAME,
                     BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD, DATABASE_URL,
                     USING_SQLITE, GENERATED_PASSWORD_FILE, BACKUP_DIR)
from .db import Base, engine, get_db, SessionLocal
from . import models
from .models import User, Run, Rectification
from .auth import (current_user, require_user, verify_password, hash_password,
                   audit, RedirectToLogin, require_csrf)
from .catalogue import SOLUTIONS
from .templating import templates
from .routers import gst8020 as gst_router, admin as admin_router, ai as ai_router

app = FastAPI(title=PORTAL_NAME, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=SESSION_HTTPS_ONLY,
                   same_site="lax", max_age=8 * 60 * 60)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
app.include_router(gst_router.router)
app.include_router(admin_router.router)
app.include_router(ai_router.router)

_LOGIN_WINDOW_SECONDS = 10 * 60
_LOGIN_FAILURE_LIMIT = 8
_login_failures = defaultdict(list)
_login_failures_lock = threading.Lock()


def _login_key(request: Request, email: str) -> tuple[str, str]:
    return ((request.client.host if request.client else "unknown"), email.strip().lower())


def _login_throttled(key: tuple[str, str]) -> bool:
    cutoff = time.monotonic() - _LOGIN_WINDOW_SECONDS
    with _login_failures_lock:
        attempts = [at for at in _login_failures[key] if at >= cutoff]
        _login_failures[key] = attempts
        return len(attempts) >= _LOGIN_FAILURE_LIMIT


def _record_login_failure(key: tuple[str, str]) -> None:
    with _login_failures_lock:
        _login_failures[key].append(time.monotonic())


def _clear_login_failures(key: tuple[str, str]) -> None:
    with _login_failures_lock:
        _login_failures.pop(key, None)


@app.middleware("http")
async def browser_security_headers(request: Request, call_next):
    """Apply a conservative baseline without changing the server-rendered UI."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; "
        "object-src 'none'; img-src 'self' data:; connect-src 'self'; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
    if SESSION_HTTPS_ONLY:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.on_event("startup")
def startup():
    """Create the tables and the first administrator, then say what happened.

    Both steps are safe to repeat: tables are only created when missing, and the
    administrator only when there are no users at all.
    """
    Base.metadata.create_all(engine)
    created = False
    with SessionLocal() as db:
        if not db.scalar(select(func.count(User.id))):
            db.add(User(email=BOOTSTRAP_ADMIN_EMAIL, name="Administrator",
                        password_hash=hash_password(BOOTSTRAP_ADMIN_PASSWORD), role="admin"))
            db.commit()
            created = True

    where = "SQLite file (single-user pilot)" if USING_SQLITE else DATABASE_URL.split("@")[-1]
    if not created:
        # Already set up. One quiet line, so a restart does not fill the screen.
        print(f"{PORTAL_NAME} — {ORG_NAME} · {where}", flush=True)
        return
    lines = [f"{PORTAL_NAME} — {ORG_NAME}", f"Database: {where}",
             "", "First administrator created:",
             f"  Email:    {BOOTSTRAP_ADMIN_EMAIL}",
             f"  Password: {BOOTSTRAP_ADMIN_PASSWORD}",
             "Change it under Administration -> Users after signing in."]
    width = max(len(x) for x in lines) + 4
    print("\n" + "=" * width)
    for line in lines:
        print("  " + line)
    print("=" * width + "\n", flush=True)


@app.exception_handler(RedirectToLogin)
def _to_login(request: Request, exc: RedirectToLogin):
    return RedirectResponse(f"/login?next={exc.path}", status_code=303)


@app.exception_handler(HTTPException)
def _http_error(request: Request, exc: HTTPException):
    with SessionLocal() as db:
        user = current_user(request, db)
        return templates.TemplateResponse(
            request, "error.html",
            {"user": user, "code": exc.status_code, "message": exc.detail,
             "solutions": SOLUTIONS}, status_code=exc.status_code)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/readyz", response_class=PlainTextResponse)
def readyz():
    """Render readiness check: process is up and the configured database answers."""
    try:
        with engine.connect() as connection:
            connection.execute(sql_text("SELECT 1"))
        return "ready"
    except SQLAlchemyError as exc:
        raise HTTPException(503, "database unavailable") from exc


# ------------------------------------------------------------------ sign in

def safe_next(path: str) -> str:
    """Only permit in-app destinations after authentication."""
    return path if path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", db: Session = Depends(get_db)):
    next = safe_next(next)
    if current_user(request, db):
        return RedirectResponse(next or "/", status_code=303)
    return templates.TemplateResponse(request, "login.html",
                                      {"next": next, "org": ORG_NAME, "portal": PORTAL_NAME})


@app.post("/login", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
def login(request: Request, email: str = Form(...), password: str = Form(...),
          next: str = Form("/"), db: Session = Depends(get_db)):
    next = safe_next(next)
    key = _login_key(request, email)
    if _login_throttled(key):
        return templates.TemplateResponse(
            request, "login.html",
            {"next": next, "org": ORG_NAME, "portal": PORTAL_NAME,
             "error": "Too many unsuccessful sign-in attempts. Wait 10 minutes and try again."},
            status_code=429, headers={"Retry-After": str(_LOGIN_WINDOW_SECONDS)})
    u = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not u or not u.is_active or not verify_password(password, u.password_hash):
        _record_login_failure(key)
        return templates.TemplateResponse(
            request, "login.html",
            {"next": next, "org": ORG_NAME, "portal": PORTAL_NAME,
             "error": "That email and password combination did not match an active account."},
            status_code=401)
    _clear_login_failures(key)
    request.session["uid"] = u.id
    u.last_login = dt.datetime.now(dt.timezone.utc)
    audit(db, u, "sign_in", "user", u.id)
    db.commit()
    return RedirectResponse(next or "/", status_code=303)


@app.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    u = current_user(request, db)
    if u:
        audit(db, u, "sign_out", "user", u.id)
        db.commit()
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ------------------------------------------------------------------- portal

@app.get("/", response_class=HTMLResponse)
def portal(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    latest = db.scalar(select(Run).where(Run.status != "superseded")
                       .order_by(Run.created_at.desc()))
    stats = (gst_router.summarise_portfolio(
        gst_router.run_rows(db, latest.id),
        json.loads(latest.config_snapshot or "{}")) if latest else None)
    open_rect = db.scalar(
        select(func.count(Rectification.id))
        .where(Rectification.status.in_(("open", "in_progress")),
               Rectification.run_id.in_(
                   select(Run.id).where(Run.status != "superseded").scalar_subquery()))) or 0
    database_location = (str(engine.url.database) if USING_SQLITE
                         else "Configured server database")
    return templates.TemplateResponse(request, "portal.html", {
        "user": user, "solutions": SOLUTIONS, "org": ORG_NAME, "portal": PORTAL_NAME,
        "latest": latest, "stats": stats, "open_rect": open_rect,
        # Shown until the administrator changes the password the installer generated.
        "first_run": user.is_admin and GENERATED_PASSWORD_FILE.exists(),
        "sqlite": USING_SQLITE,
        "database_location": database_location,
        "backup_location": str(BACKUP_DIR),
    })
