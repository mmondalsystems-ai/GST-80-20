"""Finance Operations Portal — application entry point."""

import json
import datetime as dt

from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from .config import (
    SECRET_KEY,
    SESSION_HTTPS_ONLY,
    BASE_DIR,
    ORG_NAME,
    PORTAL_NAME,
    BOOTSTRAP_ADMIN_EMAIL,
    BOOTSTRAP_ADMIN_PASSWORD,
    DATABASE_URL,
    USING_SQLITE,
    GENERATED_PASSWORD_FILE,
)

from .db import Base, engine, get_db, SessionLocal

from . import models

from .models import (
    User,
    Run,
    Rectification,
)

from .auth import (
    current_user,
    require_user,
    verify_password,
    hash_password,
    audit,
    RedirectToLogin,
    require_csrf,
)

from .catalogue import SOLUTIONS
from .templating import templates

# Keep the licence evaluator so any existing licence-related
# templates/routes continue to receive the expected status object.
from .license import evaluate as evaluate_license

from .routers import (
    gst8020 as gst_router,
    admin as admin_router,
)


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title=PORTAL_NAME,
    docs_url=None,
    redoc_url=None,
)


app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    https_only=SESSION_HTTPS_ONLY,
    same_site="lax",
    max_age=8 * 60 * 60,
)


app.mount(
    "/static",
    StaticFiles(
        directory=BASE_DIR / "app" / "static"
    ),
    name="static",
)


app.include_router(
    gst_router.router
)

app.include_router(
    admin_router.router
)


# ============================================================
# LICENCE BYPASS WRAPPER
#
# We preserve every property from the original licence object,
# but read_only is always False.
#
# This means:
#
# - Existing code expecting request.state.license still works.
# - Existing licence information does not cause import errors.
# - Missing/expired licence does NOT block POST operations.
# - Calculations remain enabled.
# ============================================================

class UnlockedLicenseStatus:

    def __init__(self, original_status):
        self._original_status = original_status


    @property
    def read_only(self):
        """
        Always allow normal application operation.
        """
        return False


    def __getattr__(self, name):
        """
        For every other licence property, use the
        original licence status object.
        """

        return getattr(
            self._original_status,
            name
        )


# ============================================================
# LICENCE MIDDLEWARE
#
# OLD BEHAVIOUR:
# Missing/expired licence caused HTTP 423 and read-only mode.
#
# NEW BEHAVIOUR:
# Licence status can still be evaluated, but it no longer
# blocks calculations or other write operations.
# ============================================================

@app.middleware("http")
async def offline_license_gate(
    request: Request,
    call_next
):

    # --------------------------------------------------------
    # Health/static requests do not need licence evaluation
    # --------------------------------------------------------

    if (
        request.url.path == "/healthz"
        or request.url.path.startswith("/static/")
    ):

        return await call_next(request)


    # --------------------------------------------------------
    # Evaluate existing licence only for compatibility
    # --------------------------------------------------------

    try:

        with SessionLocal() as db:

            original_license_status = (
                evaluate_license(db)
            )


        # ----------------------------------------------------
        # Wrap original status but force read_only=False
        # ----------------------------------------------------

        request.state.license = (
            UnlockedLicenseStatus(
                original_license_status
            )
        )


    except Exception as exc:

        # ----------------------------------------------------
        # Even if licence evaluation itself fails,
        # do not block the application.
        #
        # Provide a minimal compatible object.
        # ----------------------------------------------------

        class FallbackLicenseStatus:

            read_only = False
            status = "active"
            message = ""

        request.state.license = (
            FallbackLicenseStatus()
        )


        print(
            "Licence evaluation ignored: "
            + str(exc),
            flush=True
        )


    # --------------------------------------------------------
    # IMPORTANT:
    #
    # There is intentionally NO 423 licence blocking here.
    #
    # Continue normally.
    # --------------------------------------------------------

    return await call_next(request)


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():
    """
    Create the tables and the first administrator,
    then say what happened.

    Both steps are safe to repeat:
    tables are only created when missing,
    and the administrator only when there
    are no users at all.
    """

    Base.metadata.create_all(
        engine
    )


    created = False


    with SessionLocal() as db:

        if not db.scalar(
            select(
                func.count(User.id)
            )
        ):

            db.add(
                User(
                    email=BOOTSTRAP_ADMIN_EMAIL,
                    name="Administrator",
                    password_hash=hash_password(
                        BOOTSTRAP_ADMIN_PASSWORD
                    ),
                    role="admin",
                )
            )

            db.commit()

            created = True


    where = (
        "SQLite file (single-user pilot)"
        if USING_SQLITE
        else DATABASE_URL.split("@")[-1]
    )


    if not created:

        # Already set up.
        # One quiet line so a restart
        # does not fill the screen.

        print(
            f"{PORTAL_NAME} — "
            f"{ORG_NAME} · "
            f"{where}",
            flush=True,
        )

        return


    lines = [

        f"{PORTAL_NAME} — {ORG_NAME}",

        f"Database: {where}",

        "",

        "First administrator created:",

        f"  Email:    "
        f"{BOOTSTRAP_ADMIN_EMAIL}",

        f"  Password: "
        f"{BOOTSTRAP_ADMIN_PASSWORD}",

        (
            "Change it under "
            "Administration -> Users "
            "after signing in."
        ),

    ]


    width = (
        max(
            len(x)
            for x in lines
        )
        + 4
    )


    print(
        "\n"
        + "=" * width
    )


    for line in lines:

        print(
            "  " + line
        )


    print(
        "=" * width
        + "\n",
        flush=True,
    )


# ============================================================
# EXCEPTION HANDLERS
# ============================================================

@app.exception_handler(
    RedirectToLogin
)
def _to_login(
    request: Request,
    exc: RedirectToLogin
):

    return RedirectResponse(
        f"/login?next={exc.path}",
        status_code=303,
    )


@app.exception_handler(
    HTTPException
)
def _http_error(
    request: Request,
    exc: HTTPException
):

    with SessionLocal() as db:

        user = current_user(
            request,
            db
        )


        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "user": user,
                "code": exc.status_code,
                "message": exc.detail,
                "solutions": SOLUTIONS,
            },
            status_code=exc.status_code,
        )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get(
    "/healthz",
    response_class=PlainTextResponse
)
def healthz():

    return "ok"


# ============================================================
# SIGN IN
# ============================================================

def safe_next(path: str) -> str:
    """
    Only permit in-app destinations
    after authentication.
    """

    return (
        path
        if (
            path.startswith("/")
            and not path.startswith("//")
            and "\\" not in path
        )
        else "/"
    )


@app.get(
    "/login",
    response_class=HTMLResponse
)
def login_form(
    request: Request,
    next: str = "/",
    db: Session = Depends(get_db)
):

    next = safe_next(
        next
    )


    if current_user(
        request,
        db
    ):

        return RedirectResponse(
            next or "/",
            status_code=303,
        )


    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "next": next,
            "org": ORG_NAME,
            "portal": PORTAL_NAME,
        },
    )


@app.post(
    "/login",
    response_class=HTMLResponse,
    dependencies=[
        Depends(require_csrf)
    ],
)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):

    next = safe_next(
        next
    )


    u = db.scalar(

        select(User).where(

            User.email
            ==
            email
            .strip()
            .lower()

        )

    )


    if (
        not u
        or not u.is_active
        or not verify_password(
            password,
            u.password_hash
        )
    ):

        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "next": next,
                "org": ORG_NAME,
                "portal": PORTAL_NAME,
                "error": (
                    "That email and password "
                    "combination did not match "
                    "an active account."
                ),
            },
            status_code=401,
        )


    request.session["uid"] = u.id


    # --------------------------------------------------------
    # Licence is now always unlocked,
    # therefore this will execute normally.
    # --------------------------------------------------------

    if not request.state.license.read_only:

        u.last_login = (
            dt.datetime.now(
                dt.timezone.utc
            )
        )


        audit(
            db,
            u,
            "sign_in",
            "user",
            u.id,
        )


        db.commit()


    return RedirectResponse(
        next or "/",
        status_code=303,
    )


# ============================================================
# LOGOUT
# ============================================================

@app.get(
    "/logout"
)
def logout(
    request: Request,
    db: Session = Depends(get_db)
):

    u = current_user(
        request,
        db
    )


    if (
        u
        and not request.state.license.read_only
    ):

        audit(
            db,
            u,
            "sign_out",
            "user",
            u.id,
        )


        db.commit()


    request.session.clear()


    return RedirectResponse(
        "/login",
        status_code=303,
    )


# ============================================================
# PORTAL
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
def portal(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):

    latest = db.scalar(

        select(Run)
        .order_by(
            Run.created_at.desc()
        )

    )


    stats = (

        gst_router.summarise_portfolio(

            gst_router.run_rows(
                db,
                latest.id
            ),

            json.loads(
                latest.config_snapshot
                or "{}"
            ),

        )

        if latest

        else None

    )


    open_rect = (

        db.scalar(

            select(
                func.count(
                    Rectification.id
                )
            )

            .where(

                Rectification.status.in_(
                    (
                        "open",
                        "in_progress",
                    )
                ),

                Rectification.run_id.in_(

                    select(Run.id)

                    .where(
                        Run.status
                        !=
                        "superseded"
                    )

                    .scalar_subquery()

                ),

            )

        )

        or 0

    )


    return templates.TemplateResponse(

        request,

        "portal.html",

        {

            "user": user,

            "solutions": SOLUTIONS,

            "org": ORG_NAME,

            "portal": PORTAL_NAME,

            "latest": latest,

            "stats": stats,

            "open_rect": open_rect,

            # Shown until the administrator changes
            # the password the installer generated.
            "first_run": (
                user.is_admin
                and GENERATED_PASSWORD_FILE.exists()
            ),

            "sqlite": USING_SQLITE,

        },

    )
