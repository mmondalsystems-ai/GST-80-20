"""The GST 80:20 module."""
import io
import json
import datetime as dt
import math
from collections import defaultdict

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse
from sqlalchemy import select, func, delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (User, Run, RunRow, Rectification, LedgerOverride, Creditor, Setting)
from ..auth import require_user, require_editor, audit, require_csrf
from ..templating import templates
from ..catalogue import SOLUTIONS
from ..config import UPLOAD_DIR
from ..backup import backup_after_run
from ..engine import readers as R
from ..engine.golden import calculate, voucher_key, INELIGIBLE_KEYWORDS
from ..engine.calc import (summarise, summarise_by_project, vendor_concentration,
                           summarise_portfolio, report_table, merged_config, DEFAULTS)
from ..engine.gstin import validate as validate_gstin
from ..engine import export as X

router = APIRouter(prefix="/gst8020", tags=["gst8020"])

RESOLUTIONS = {
    "resolved_registered":   "Resolved — GSTIN confirmed, treat as Registered",
    "resolved_unregistered": "Resolved — supplier is genuinely unregistered",
    "resolved_exempt":       "Resolved — supply is GST-exempt, no GST expected",
    "wont_fix":              "No action — leave as is",
}
RESOLVED_STATUSES = set(RESOLUTIONS)


# ------------------------------------------------------------------ settings

def load_config(db: Session) -> dict:
    row = db.get(Setting, "gst8020.config")
    cfg = merged_config(json.loads(row.value) if row and row.value else {})
    # Historical settings may contain a user-entered list. The source script,
    # not that setting, now controls keyword classification for every new run.
    cfg["ineligible_keywords"] = INELIGIBLE_KEYWORDS.copy()
    return cfg


def save_config(db: Session, cfg: dict, user: User):
    row = db.get(Setting, "gst8020.config")
    before = json.loads(row.value) if row and row.value else {}
    if not row:
        row = Setting(key="gst8020.config")
        db.add(row)
    row.value = json.dumps(cfg)
    row.updated_by_id = user.id
    audit(db, user, "update_masters", "settings", "gst8020.config", before=before, after=cfg)


def ctx(request, user, **kw):
    base = {"user": user, "solutions": SOLUTIONS, "module": "gst8020",
            "resolutions": RESOLUTIONS}
    base.update(kw)
    return base


# ---------------------------------------------------------------- overview

@router.get("", response_class=HTMLResponse)
def overview(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    runs = list(db.scalars(select(Run).order_by(Run.created_at.desc()).limit(24)))
    cfg = load_config(db)
    fy_rows = fy_summary(db, cfg)
    open_rect = db.scalar(
        select(func.count(Rectification.id))
        .where(Rectification.status.in_(("open", "in_progress")),
               Rectification.run_id.in_(
                   select(Run.id).where(Run.status != "superseded").scalar_subquery()))) or 0
    return templates.TemplateResponse(request, "gst/overview.html", ctx(
        request, user, runs=runs, fy=fy_rows, cfg=cfg, open_rect=open_rect,
        creditor_count=db.scalar(select(func.count(Creditor.id))) or 0))


def fy_summary(db: Session, cfg: dict):
    """Year-to-date position per financial year and project.

    The 80% test is a financial-year, project-wise test, so month-end figures
    are only an indicator: this is the number that decides the position.
    """
    runs = list(db.scalars(select(Run).where(Run.status != "superseded")
                           .order_by(Run.period_start)))
    by_fy = defaultdict(list)
    for r in runs:
        by_fy[r.financial_year].append(r)
    out = []
    for fy, rs in sorted(by_fy.items(), reverse=True):
        ids = [r.id for r in rs]
        rows = [dict(voucher_id=x.voucher_id, head=x.head, project=x.project,
                     eligibility=x.eligibility, gst_status=x.gst_status, closing=x.closing,
                     ledger=x.ledger, gstin=x.gstin, month=x.month)
                for x in db.scalars(select(RunRow).where(RunRow.run_id.in_(ids)))]
        total = summarise_portfolio(rows, cfg)
        months = []
        for r in rs:
            mrows = [x for x in rows if x["month"] == r.period_month]
            months.append({"run": r, "s": summarise_portfolio(mrows, cfg)})
        out.append({"fy": fy, "runs": rs, "total": total,
                    "projects": summarise_by_project(rows, cfg), "months": months})
    return out


@router.get("/year", response_class=HTMLResponse)
def year_view(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    cfg = load_config(db)
    return templates.TemplateResponse(request, "gst/year.html",
                                      ctx(request, user, fy=fy_summary(db, cfg), cfg=cfg))


# ------------------------------------------------------------------ new run

@router.get("/new", response_class=HTMLResponse)
def new_run_form(request: Request, user: User = Depends(require_editor),
                 db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "gst/new.html",
                                      ctx(request, user, cfg=load_config(db)))


@router.post("/new", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
async def new_run(request: Request,
                  daybook: UploadFile = File(...),
                  voucher: UploadFile = File(...),
                  creditors: UploadFile = File(...),
                  label: str = Form(""),
                  user: User = Depends(require_editor), db: Session = Depends(get_db)):
    cfg = load_config(db)
    def reject(message: str, status_code: int = 400):
        return templates.TemplateResponse(request, "gst/new.html",
            ctx(request, user, cfg=cfg, error=message), status_code=status_code)
    try:
        db_rows = R.read_daybook(io.BytesIO(await daybook.read()))
        sv_rows = R.read_voucher(io.BytesIO(await voucher.read()))
    except R.IngestError as e:
        return reject(str(e))
    # Do not turn a missing/renamed numeric column or uncached text value into
    # a credible-looking zero-spend month.  The approved script expects typed
    # Excel numbers; blanks within a populated amount column still mean zero.
    for label, source_rows, fields in (
        ("Day Book", db_rows, ("Debit", "Credit")),
        ("Search Voucher", sv_rows, ("Debit_Amount", "Credit_Amount")),
    ):
        if not any(r.get(field) not in (None, "") for r in source_rows for field in fields):
            return reject(f"{label}: both amount columns are empty. Check the Tally export.")
        for number, row in enumerate(source_rows, 1):
            for field in fields:
                value = row.get(field)
                if value not in (None, "") and (isinstance(value, bool) or
                        not isinstance(value, (int, float)) or not math.isfinite(value)):
                    return reject(f"{label}: {field} on data row {number} is not an Excel number.")
    approved = [r for r in sv_rows if str(r.get("Status", "")).strip().lower() == "approved"]
    if not approved:
        return reject("Search Voucher has no Approved rows. Check the file and Status column.")
    if any(R.parse_date(r.get("Voucher_Date")) is None for r in approved):
        return reject("Every Approved Search Voucher row needs a valid Voucher_Date.")
    # A monthly run must contain one calendar month.  Check before touching
    # shared masters, including when the requested month is already frozen.
    dates = [R.parse_date(r.get("Date")) for r in db_rows]
    if any(d is None for d in dates):
        return templates.TemplateResponse(request, "gst/new.html",
            ctx(request, user, cfg=cfg, error="Every Day Book row needs a valid Date."),
            status_code=400)
    months = {(d.year, d.month) for d in dates}
    if len(months) != 1:
        return templates.TemplateResponse(request, "gst/new.html",
            ctx(request, user, cfg=cfg,
                error="The Day Book contains more than one calendar month. Upload one month at a time."),
            status_code=400)
    only_date = dates[0]
    requested_period = only_date.strftime("%b-%y")
    requested_fy = f"{only_date.year}-{only_date.year + 1}" if only_date.month >= 4 else f"{only_date.year - 1}-{only_date.year}"
    if db.scalar(select(Run).where(Run.period_month == requested_period,
                                    Run.financial_year == requested_fy,
                                    Run.status == "frozen")):
        return templates.TemplateResponse(request, "gst/new.html", ctx(
            request, user, cfg=cfg,
            error=f"{requested_period} {requested_fy} is frozen. No replacement calculation can be created."),
            status_code=409)

    try:
        imported = R.read_creditors(io.BytesIO(await creditors.read()))
    except R.IngestError as e:
        return reject(str(e))

    # The legacy script always calculates from the supplied three workbooks.
    # The shared master is refreshed for workflow use, but is never substituted
    # for this run's source export (which would make identical inputs drift).
    try:
        result = calculate(db_rows, sv_rows, imported, cfg)
    except (ValueError, TypeError) as e:
        return reject(f"Calculation stopped because an input value is invalid: {e}")
    rows = result["rows"]
    if not rows:
        return templates.TemplateResponse(
            request, "gst/new.html",
            ctx(request, user, cfg=cfg,
                error="No reportable lines came out of these files with the current filters. "
                      "Check the included Fixed Group Names under Masters."), status_code=400)
    approved_keys = {
        voucher_key(R.parse_date(r["Voucher_Date"]), r.get("Voucher_Type"), r.get("Voucher_No"))
        for r in approved
    }
    unmatched = {r["voucher_id"] for r in rows} - approved_keys
    if unmatched:
        examples = ", ".join(sorted(unmatched)[:3])
        return reject(f"{len(unmatched)} reportable voucher(s) are missing from Approved Search Voucher rows. "
                      f"Check that the three exports cover the same month. Examples: {examples}.")

    period = rows[0]["month"]
    first_date = min(r["voucher_date"] for r in rows)
    fy = rows[0]["year"]

    _, _, dupes = import_creditors(db, imported, user)
    if dupes:
        request.session["flash"] = (
            f"{len(dupes)} supplier name(s) appeared more than once in the creditors export "
            f"and were merged: {', '.join(dupes[:5])}"
            + ("…" if len(dupes) > 5 else "")
            + " — worth tidying in Tally.")

    # An earlier draft for the same month is kept, but superseded.
    for old in db.scalars(select(Run).where(Run.period_month == period,
                                            Run.financial_year == fy,
                                            Run.status == "draft")):
        changed = db.execute(update(Run).where(Run.id == old.id, Run.status == "draft")
                             .values(status="superseded")).rowcount
        if changed != 1:
            db.rollback()
            return templates.TemplateResponse(request, "gst/new.html", ctx(
                request, user, cfg=cfg,
                error="This month changed while the upload was running. Please refresh and retry."),
                status_code=409)
        audit(db, user, "supersede_run", "run", old.id, detail=f"replaced by a new {period} run")

    stats = summarise_portfolio(rows, cfg)
    stats["by_project"] = summarise_by_project(rows, cfg)
    run = Run(label=label.strip() or f"{period} calculation",
              period_month=period, period_start=first_date.replace(day=1), financial_year=fy,
              created_by_id=user.id,
              source_files=json.dumps({"daybook": daybook.filename, "voucher": voucher.filename,
                                       "creditors": creditors.filename}),
              config_snapshot=json.dumps(cfg), stats=json.dumps(stats, default=str))
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return templates.TemplateResponse(request, "gst/new.html", ctx(
            request, user, cfg=cfg,
            error="Another calculation for this month was created concurrently. Please refresh."),
            status_code=409)

    for r in rows:
        db.add(RunRow(run_id=run.id, voucher_id=r["voucher_id"], head=r["head"], year=r["year"],
                      month=r["month"], voucher_date=r["voucher_date"],
                      voucher_type=r["voucher_type"], voucher_no=r["voucher_no"],
                      bill_no=r["bill_no"][:60], bill_date=r["bill_date"], project=r["project"][:120],
                      narration=r["source_narration"], account_head=r["account_head"][:160],
                      account_name=r["account_name"][:200], ledger=r["ledger"][:200],
                      ledger_raw=r["ledger_raw"][:200], gstin=r["gstin"][:20],
                      debit=r["debit"], credit=r["credit"], closing=r["closing"],
                      formula_key=r["formula_key"][:60], eligibility=r["eligibility"],
                      gst_status=r["gst_status"], rcm=r["rcm"],
                      party_kind=r["party_kind"][:40], party_source=r["party_source"][:60],
                      flags=r["flags"]))

    # The golden script de-duplicates on (voucher, issue), not voucher alone.
    for item in result["rectifications"]:
        prior = db.scalar(select(Rectification)
                          .where(Rectification.voucher_id == item["voucher_id"],
                                 Rectification.reason == item["reason"],
                                 Rectification.status.in_(tuple(RESOLVED_STATUSES)))
                          .order_by(Rectification.resolved_at.desc()))
        db.add(Rectification(run_id=run.id, voucher_id=item["voucher_id"],
                             ledger=item["ledger"][:200], account_name=item["account_name"][:200],
                             project=item["project"][:120], amount=item["amount"],
                             reason=item["reason"], suggestions=item.get("suggestions", ""),
                             status=prior.status if prior else "open",
                             resolution_note=(f"Carried forward from {prior.voucher_id}: "
                                              f"{prior.resolution_note}") if prior else "",
                             assigned_to_id=prior.assigned_to_id if prior else None))

    audit(db, user, "create_run", "run", run.id,
          after={"period": period, "rows": len(rows), "pct": round(stats["pct"], 2)},
          detail=f"{daybook.filename} + {voucher.filename}")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return templates.TemplateResponse(request, "gst/new.html", ctx(
            request, user, cfg=cfg,
            error="This month changed while the upload was running. Please refresh."),
            status_code=409)
    try:
        _, request.session["backup_notice"] = backup_after_run()
    except (OSError, RuntimeError, ValueError) as exc:
        request.session["backup_notice"] = (
            f"Calculation saved, but its database backup FAILED: {exc}")
    return RedirectResponse(f"/gst8020/runs/{run.id}", status_code=303)


def import_creditors(db: Session, imported, user: User):
    """Refresh the master from an export without losing manual corrections.

    The Tally export can hold the same supplier more than once under different
    capitalisation ("Chhaya Enterprises" and "CHHAYA ENTERPRISES"). Those are
    merged into one record here -- the first spelling wins, and any GSTIN or PAN
    found on either row is kept -- and the duplicates are reported so the
    accounts team can tidy the Tally master itself.
    """
    added = updated = 0
    duplicates = []
    pending = {}
    for c in imported:
        name = R.norm(c.get("Account_Name"))
        if not name:
            continue
        k = R.key(name)
        gstin = R.norm(c.get("GSTIN"))
        pan = R.norm(c.get("PAN_no"))
        row = pending.get(k) or db.scalar(select(Creditor).where(Creditor.name_key == k))
        if row is None:
            row = Creditor(name_key=k, account_name=name)
            db.add(row)
            added += 1
        else:
            if k in pending:
                duplicates.append(name)
                # Keep whichever spelling carried the detail.
                if gstin and not row.gstin:
                    row.gstin = gstin
                if pan and not row.pan:
                    row.pan = pan
                row.gstin_valid = validate_gstin(row.gstin, row.pan)[0] if row.gstin else ""
                continue
            updated += 1
        pending[k] = row
        row.legal_name = R.norm(c.get("Legal_Cheque_Name"))
        row.group_name = R.norm(c.get("Group_Name"))
        row.pan = pan
        # A GSTIN filled in here by the accounts team is not overwritten by a
        # blank in the next export.
        if gstin or row.source != "manual":
            if gstin:
                row.gstin = gstin
        row.gstin_valid = validate_gstin(row.gstin, row.pan)[0] if row.gstin else ""
    audit(db, user, "import_creditors", "creditor", "",
          after={"added": added, "updated": updated, "duplicate_names": len(duplicates)},
          detail=("Duplicate supplier names merged: " + ", ".join(duplicates[:20]))
          if duplicates else "")
    return added, updated, duplicates


# ------------------------------------------------------------------ run view

def get_run(db, run_id) -> Run:
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "That calculation run no longer exists.")
    return run


def run_rows(db, run_id):
    return [dict(voucher_id=x.voucher_id, head=x.head, project=x.project, month=x.month,
                 eligibility=x.eligibility, gst_status=x.gst_status, closing=x.closing,
                 ledger=x.ledger, gstin=x.gstin) for x in
            db.scalars(select(RunRow).where(RunRow.run_id == run_id))]


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: int, user: User = Depends(require_user),
               db: Session = Depends(get_db)):
    run = get_run(db, run_id)
    cfg = merged_config(json.loads(run.config_snapshot or "{}"))
    rows = run_rows(db, run_id)
    stats = summarise_portfolio(rows, cfg)
    open_rect = db.scalar(select(func.count(Rectification.id))
                          .where(Rectification.run_id == run_id,
                                 Rectification.status.in_(("open", "in_progress")))) or 0
    backup_notice = request.session.pop("backup_notice", None)
    return templates.TemplateResponse(request, "gst/run.html", ctx(
        request, user, run=run, stats=stats, cfg=cfg, table=report_table(rows, cfg),
        projects=summarise_by_project(rows, cfg), open_rect=open_rect,
        files=json.loads(run.source_files or "{}"), backup_notice=backup_notice))


@router.get("/runs/{run_id}/rows", response_class=HTMLResponse)
def run_table(request: Request, run_id: int, q: str = "", status: str = "", elig: str = "",
              project: str = "", flagged: str = "", page: int = Query(1, ge=1),
              user: User = Depends(require_user), db: Session = Depends(get_db)):
    run = get_run(db, run_id)
    stmt = select(RunRow).where(RunRow.run_id == run_id)
    if status:
        stmt = stmt.where(RunRow.gst_status == status)
    if elig:
        stmt = stmt.where(RunRow.eligibility == elig)
    if project:
        stmt = stmt.where(RunRow.project == project)
    if flagged:
        stmt = stmt.where(RunRow.flags != "")
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(RunRow.ledger.ilike(like) | RunRow.account_name.ilike(like) |
                          RunRow.voucher_id.ilike(like) | RunRow.narration.ilike(like) |
                          RunRow.gstin.ilike(like) | RunRow.bill_no.ilike(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    per = 100
    rows = list(db.scalars(stmt.order_by(RunRow.voucher_date, RunRow.id)
                           .offset((page - 1) * per).limit(per)))
    projects = [p for (p,) in db.execute(select(RunRow.project).where(RunRow.run_id == run_id)
                                         .distinct().order_by(RunRow.project))]
    return templates.TemplateResponse(request, "gst/rows.html", ctx(
        request, user, run=run, rows=rows, total=total, page=page, per=per,
        pages=max(1, -(-total // per)), q=q, status=status, elig=elig, project=project,
        flagged=flagged, projects=projects))


@router.get("/runs/{run_id}/vendors", response_class=HTMLResponse)
def vendors(request: Request, run_id: int, status: str = "Unregistered",
            user: User = Depends(require_user), db: Session = Depends(get_db)):
    run = get_run(db, run_id)
    rows = [dict(head=x.head, eligibility=x.eligibility, gst_status=x.gst_status,
                 closing=x.closing, ledger=x.ledger, gstin=x.gstin, party_kind=x.party_kind,
                 voucher_id=x.voucher_id, project=x.project)
            for x in db.scalars(select(RunRow).where(RunRow.run_id == run_id))]
    cfg = merged_config(json.loads(run.config_snapshot or "{}"))
    stats = summarise_portfolio(rows, cfg)
    clusters = vendor_concentration(rows, status=status)
    running, cum = [], 0.0
    total = sum(c["amount"] for c in clusters) or 1
    for c in clusters:
        cum += c["amount"]
        running.append({**c, "cum_pct": cum / total * 100})
    return templates.TemplateResponse(request, "gst/vendors.html", ctx(
        request, user, run=run, clusters=running, status=status, stats=stats,
        total=total, threshold=cfg['threshold_pct']))


@router.get("/runs/{run_id}/exceptions", response_class=HTMLResponse)
def exceptions(request: Request, run_id: int, user: User = Depends(require_user),
               db: Session = Depends(get_db)):
    run = get_run(db, run_id)
    rows = list(db.scalars(select(RunRow).where(RunRow.run_id == run_id, RunRow.flags != "")))
    groups = defaultdict(list)
    for r in rows:
        for f in r.flags.split(","):
            if f:
                groups[f].append(r)
    credit_lines = list(db.scalars(select(RunRow).where(RunRow.run_id == run_id,
                                                        RunRow.credit > 0, RunRow.debit == 0)))
    if credit_lines:
        groups["credit_side_cost_line"] = credit_lines
    labels = {
        "suspect_party": ("Party looks wrong",
                          "The resolved party is a round-off or suspense ledger, not a supplier."),
        "no_party_line": ("No party line found",
                          "The voucher has no credit line outside the tax ledgers."),
        "unmatched_voucher": ("Not in Search Voucher",
                              "No matching Approved voucher — check the export covers the same period."),
        "manual_override": ("Manually corrected party",
                            "A person has overridden the resolved ledger name for these rows."),
        "credit_side_cost_line": ("Credit-side cost line",
                                  "Usually a month-end reallocation JV. It reduces whichever bucket it lands in."),
        "cement_rcm_carveout": ("Cement RCM carve-out",
                                "Cement from an unregistered dealer is not deemed registered, so the "
                                "RCM override was not applied to these rows."),
    }
    for code in list(groups):
        if code.startswith("gstin_"):
            labels[code] = ("GSTIN failed validation — " + code[6:].replace("_", " "),
                            "The GSTIN on file is structurally invalid. The approved Python still "
                            "classifies a populated GSTIN as Registered; confirm and correct the source.")
    ordered = sorted(groups.items(), key=lambda kv: -sum(abs(r.closing) for r in kv[1]))
    return templates.TemplateResponse(request, "gst/exceptions.html", ctx(
        request, user, run=run, groups=ordered, labels=labels))


# --------------------------------------------------------- rectification queue

@router.get("/rectifications", response_class=HTMLResponse)
def rectifications(request: Request, run_id: int | None = None, status: str = "open",
                   mine: str = "", user: User = Depends(require_user),
                   db: Session = Depends(get_db)):
    live_runs = select(Run.id).where(Run.status != "superseded").scalar_subquery()
    stmt = select(Rectification).where(Rectification.run_id.in_(live_runs))
    if run_id:
        stmt = stmt.where(Rectification.run_id == run_id)
    if status == "open":
        stmt = stmt.where(Rectification.status.in_(("open", "in_progress")))
    elif status and status != "all":
        stmt = stmt.where(Rectification.status == status)
    if mine:
        stmt = stmt.where(Rectification.assigned_to_id == user.id)
    items = list(db.scalars(stmt.order_by(Rectification.amount.desc()).limit(500)))
    flash = request.session.pop("flash", None)
    users = list(db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.name)))
    counts = dict(db.execute(select(Rectification.status, func.count(Rectification.id))
                             .where(Rectification.run_id.in_(live_runs))
                             .group_by(Rectification.status)).all())
    open_value = sum(i.amount for i in items if i.status in ("open", "in_progress"))
    return templates.TemplateResponse(request, "gst/rectifications.html", ctx(
        request, user, items=items, users=users, counts=counts, status=status, flash=flash,
        run_id=run_id, mine=mine, open_value=open_value,
        runs=list(db.scalars(select(Run).order_by(Run.created_at.desc()).limit(24)))))


@router.post("/rectifications/{item_id}", dependencies=[Depends(require_csrf)])
def update_rectification(item_id: int, request: Request,
                         status: str = Form(...), note: str = Form(""),
                         assigned_to: str = Form(""), gstin: str = Form(""),
                         user: User = Depends(require_editor), db: Session = Depends(get_db)):
    item = db.get(Rectification, item_id)
    if not item:
        raise HTTPException(404, "That rectification item no longer exists.")
    run = get_run(db, item.run_id)
    if run.status != "draft":
        raise HTTPException(409, "Only a draft month's review decisions can be changed.")
    # Take the same run-row write lock as freeze.  A decision cannot commit
    # after another request has frozen the month.
    changed = db.execute(update(Run).where(Run.id == run.id, Run.status == "draft")
                         .values(status="draft")).rowcount
    if changed != 1:
        db.rollback()
        raise HTTPException(409, "This month was frozen while you were editing. Refresh the page.")
    before = {"status": item.status, "note": item.resolution_note,
              "assigned_to": item.assigned_to_id}
    if status not in RESOLUTIONS and status not in ("open", "in_progress"):
        raise HTTPException(400, "Unknown resolution.")

    # This is the follow-up master.  Calculation remains tied to the newly
    # uploaded Creditors export until that export itself contains the GSTIN.
    gstin = gstin.strip().upper()
    if gstin:
        code, message = validate_gstin(gstin)
        if code != "ok":
            # A mistyped GSTIN is an everyday slip, so the person goes back to the
            # form they were filling in rather than to an error page.
            request.session["flash"] = (f"Nothing was saved for {item.ledger}: {message}. "
                                        f"Check the number and try again.")
            return RedirectResponse(request.headers.get("referer",
                                                        "/gst8020/rectifications"), status_code=303)
        k = R.key(item.ledger)
        row = db.scalar(select(Creditor).where(Creditor.name_key == k))
        if row is None:
            row = Creditor(name_key=k, account_name=item.ledger)
            db.add(row)
        old = row.gstin
        row.gstin, row.source, row.gstin_valid = gstin, "manual", "ok"
        row.updated_by_id = user.id
        audit(db, user, "set_gstin", "creditor", item.ledger,
              before={"gstin": old}, after={"gstin": gstin},
              detail=f"entered while resolving {item.voucher_id}")

    item.status = status
    item.resolution_note = note.strip()
    item.assigned_to_id = int(assigned_to) if assigned_to else None
    if status in RESOLVED_STATUSES:
        item.resolved_by_id, item.resolved_at = user.id, dt.datetime.now(dt.timezone.utc)
    audit(db, user, "resolve_rectification", "rectification", item.id,
          before=before, after={"status": status, "note": note})
    db.commit()
    return RedirectResponse(request.headers.get("referer", "/gst8020/rectifications"),
                            status_code=303)


# ------------------------------------------------------------------ overrides

@router.post("/overrides", dependencies=[Depends(require_csrf)])
def set_override(request: Request, raw: str = Form(...), ledger: str = Form(...),
                 party_kind: str = Form("Vendor"), note: str = Form(""),
                 user: User = Depends(require_editor), db: Session = Depends(get_db)):
    raise HTTPException(409, "Ledger overrides are disabled: the supplied 80-20.py controls party resolution. Correct the source exports or approve a revised Python script.")


@router.get("/overrides", response_class=HTMLResponse)
def list_overrides(request: Request, raw: str = "", user: User = Depends(require_user),
                   db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "gst/overrides.html", ctx(
        request, user, raw=raw[:200], items=list(db.scalars(select(LedgerOverride)
                                             .order_by(LedgerOverride.updated_at.desc())))))


# ------------------------------------------------------------------- freezing

@router.post("/runs/{run_id}/freeze", dependencies=[Depends(require_csrf)])
def freeze_run(run_id: int, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    run = get_run(db, run_id)
    if run.status != "draft":
        raise HTTPException(409, "Only a draft calculation can be frozen.")
    frozen_at = dt.datetime.now(dt.timezone.utc)
    changed = db.execute(update(Run).where(Run.id == run_id, Run.status == "draft")
                         .values(status="frozen", frozen_at=frozen_at)).rowcount
    if changed != 1:
        db.rollback()
        raise HTTPException(409, "This calculation changed while freezing. Please refresh.")
    audit(db, user, "freeze_run", "run", run.id,
          after={"status": "frozen", "frozen_at": frozen_at.isoformat()},
          detail=f"{run.period_month} permanently frozen")
    db.commit()
    return RedirectResponse(f"/gst8020/runs/{run_id}", status_code=303)


# ------------------------------------------------------------------- exports

@router.get("/runs/{run_id}/export.xlsx")
def export_run(run_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    run = get_run(db, run_id)
    buf = X.build_workbook(db, run)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f'attachment; filename="80-20_{run.period_month.replace(" ", "")}.xlsx"'})


@router.get("/creditors/missing-gstin.xlsx")
def missing_gstin(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """The fill-in sheet the accounts team completes and imports back."""
    buf = X.missing_gstin_workbook(db)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="GSTIN_to_complete.xlsx"'})


@router.post("/creditors/import-gstin", dependencies=[Depends(require_csrf)])
async def import_gstin(request: Request, sheet: UploadFile = File(...),
                       user: User = Depends(require_editor), db: Session = Depends(get_db)):
    try:
        rows = R.read_sheet(io.BytesIO(await sheet.read()), ["ACCOUNT_NAME", "GSTIN"],
                            "GSTIN completion sheet")
    except R.IngestError as e:
        raise HTTPException(400, str(e))
    applied, rejected = 0, []
    for r in rows:
        name, g = R.norm(r.get("Account_Name")), R.norm(r.get("GSTIN")).upper()
        if not name or not g:
            continue
        code, message = validate_gstin(g, R.norm(r.get("PAN_no")))
        if code != "ok":
            rejected.append(f"{name}: {message}")
            continue
        k = R.key(name)
        row = db.scalar(select(Creditor).where(Creditor.name_key == k))
        if row is None:
            row = Creditor(name_key=k, account_name=name)
            db.add(row)
        row.gstin, row.source, row.gstin_valid, row.updated_by_id = g, "manual", "ok", user.id
        applied += 1
    audit(db, user, "import_gstin", "creditor", "",
          after={"applied": applied, "rejected": len(rejected)})
    db.commit()
    request.session["flash"] = (f"{applied} GSTIN(s) saved to the creditors master."
                                + (f" {len(rejected)} rejected: " + "; ".join(rejected[:5])
                                   if rejected else ""))
    return RedirectResponse("/gst8020/creditors", status_code=303)


@router.get("/creditors", response_class=HTMLResponse)
def creditors_view(request: Request, q: str = "", show: str = "missing",
                   user: User = Depends(require_user), db: Session = Depends(get_db)):
    stmt = select(Creditor)
    if show == "missing":
        stmt = stmt.where((Creditor.gstin == "") | (Creditor.gstin.is_(None)))
    elif show == "invalid":
        stmt = stmt.where(Creditor.gstin != "", Creditor.gstin_valid != "ok")
    if q:
        stmt = stmt.where(Creditor.account_name.ilike(f"%{q.strip()}%"))
    items = list(db.scalars(stmt.order_by(Creditor.account_name).limit(400)))
    counts = {
        "all": db.scalar(select(func.count(Creditor.id))) or 0,
        "missing": db.scalar(select(func.count(Creditor.id)).where(Creditor.gstin == "")) or 0,
        "invalid": db.scalar(select(func.count(Creditor.id))
                             .where(Creditor.gstin != "", Creditor.gstin_valid != "ok")) or 0,
    }
    flash = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "gst/creditors.html", ctx(
        request, user, items=items, counts=counts, show=show, q=q, flash=flash))


@router.post("/creditors/{creditor_id}", dependencies=[Depends(require_csrf)])
def update_creditor(creditor_id: int, request: Request, gstin: str = Form(""),
                    user: User = Depends(require_editor), db: Session = Depends(get_db)):
    row = db.get(Creditor, creditor_id)
    if not row:
        raise HTTPException(404, "That creditor is not in the master.")
    g = gstin.strip().upper()
    if g:
        code, message = validate_gstin(g, row.pan)
        if code != "ok":
            request.session["flash"] = (f"Nothing was saved for {row.account_name}: {message}.")
            return RedirectResponse(request.headers.get("referer", "/gst8020/creditors"),
                                    status_code=303)
    audit(db, user, "set_gstin", "creditor", row.account_name,
          before={"gstin": row.gstin}, after={"gstin": g})
    row.gstin, row.source, row.updated_by_id = g, "manual", user.id
    row.gstin_valid = validate_gstin(g, row.pan)[0] if g else ""
    db.commit()
    return RedirectResponse(request.headers.get("referer", "/gst8020/creditors"), status_code=303)


# ------------------------------------------------------------------- masters

@router.get("/masters", response_class=HTMLResponse)
def masters(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "gst/masters.html", ctx(
        request, user, cfg=load_config(db), defaults=DEFAULTS))


@router.post("/masters", dependencies=[Depends(require_csrf)])
async def save_masters(request: Request, user: User = Depends(require_editor),
                       db: Session = Depends(get_db)):
    form = await request.form()
    cfg = load_config(db)
    if "ineligible_keywords" in form:
        raise HTTPException(409, "Eligibility keywords are fixed by the supplied 80-20.py.")
    for field in ("include_fixed_groups", "exclude_parent_groups"):
        if field in form:
            cfg[field] = [line.strip() for line in str(form[field]).splitlines() if line.strip()]
    for field in ("shortfall_tax_rate", "threshold_pct", "alert_buffer_pct"):
        if field in form:
            try:
                cfg[field] = float(form[field])
            except ValueError:
                raise HTTPException(400, f"{field.replace('_', ' ')} must be a number.")
    save_config(db, cfg, user)
    db.commit()
    return RedirectResponse("/gst8020/masters", status_code=303)
