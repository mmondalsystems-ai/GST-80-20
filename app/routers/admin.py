"""User administration and the audit trail."""
from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User, AuditLog
from ..auth import require_user, require_admin, hash_password, audit, ROLES, require_csrf
from ..config import BOOTSTRAP_ADMIN_EMAIL, GENERATED_PASSWORD_FILE
from ..templating import templates
from ..catalogue import SOLUTIONS

router = APIRouter(prefix="/admin", tags=["admin"])


def ctx(user, **kw):
    base = {"user": user, "solutions": SOLUTIONS, "module": "admin", "roles": ROLES}
    base.update(kw)
    return base


@router.get("/users", response_class=HTMLResponse)
def users(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    flash = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "admin/users.html", ctx(
        user, users=list(db.scalars(select(User).order_by(User.name))), flash=flash))


@router.post("/users", dependencies=[Depends(require_csrf)])
def create_user(request: Request, name: str = Form(...), email: str = Form(...),
                password: str = Form(...), role: str = Form("viewer"),
                user: User = Depends(require_admin), db: Session = Depends(get_db)):
    email = email.strip().lower()
    if role not in ROLES:
        raise HTTPException(400, "Unknown role.")
    # These are ordinary mistakes rather than failures, so they come back as a
    # message on the page the person is already on.
    if len(password) < 10:
        request.session["flash"] = "Choose a password of at least 10 characters."
        return RedirectResponse("/admin/users", status_code=303)
    if db.scalar(select(User).where(User.email == email)):
        request.session["flash"] = (f"An account already exists for {email}. "
                                    f"Change that account's role below instead.")
        return RedirectResponse("/admin/users", status_code=303)
    u = User(name=name.strip(), email=email, role=role, password_hash=hash_password(password))
    db.add(u)
    db.flush()
    audit(db, user, "create_user", "user", u.id, after={"email": email, "role": role})
    db.commit()
    request.session["flash"] = f"Account created for {name.strip()}."
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}", dependencies=[Depends(require_csrf)])
def update_user(user_id: int, request: Request, role: str = Form(""), active: str = Form(""),
                password: str = Form(""), user: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "That account no longer exists.")
    before = {"role": u.role, "is_active": u.is_active}
    if role and role in ROLES:
        if u.id == user.id and role != "admin":
            raise HTTPException(400, "You cannot remove your own administrator role. "
                                     "Ask another administrator to do it.")
        u.role = role
    if password:
        if len(password) < 10:
            raise HTTPException(400, "Choose a password of at least 10 characters.")
        u.password_hash = hash_password(password)
        audit(db, user, "reset_password", "user", u.id)
        # The install writes the generated first password to disk so somebody can
        # sign in. Once it has been changed, that file is no longer needed.
        if u.email == BOOTSTRAP_ADMIN_EMAIL and GENERATED_PASSWORD_FILE.exists():
            try:
                GENERATED_PASSWORD_FILE.unlink()
            except OSError:
                pass
    new_active = active == "1"
    if u.id == user.id and not new_active:
        raise HTTPException(400, "You cannot deactivate your own account.")
    u.is_active = new_active
    audit(db, user, "update_user", "user", u.id, before=before,
          after={"role": u.role, "is_active": u.is_active})
    db.commit()
    request.session["flash"] = f"{u.name} updated."
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/audit", response_class=HTMLResponse)
def audit_trail(request: Request, q: str = "", action: str = "", page: int = Query(1, ge=1),
                user: User = Depends(require_user), db: Session = Depends(get_db)):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(AuditLog.user_label.ilike(like), AuditLog.entity_id.ilike(like),
                              AuditLog.detail.ilike(like), AuditLog.after.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    per = 80
    entries = list(db.scalars(stmt.order_by(AuditLog.at.desc())
                              .offset((page - 1) * per).limit(per)))
    actions = [a for (a,) in db.execute(select(AuditLog.action).distinct()
                                        .order_by(AuditLog.action))]
    return templates.TemplateResponse(request, "admin/audit.html", ctx(
        user, entries=entries, total=total, page=page, pages=max(1, -(-total // per)),
        q=q, action=action, actions=actions))
