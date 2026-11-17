"""Central AI Workspace: feature controls, Anthropic actions and FinOps."""
import datetime as dt
import difflib
import json
from collections import defaultdict

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (User, Run, RunRow, Rectification, Creditor, AISetting,
                      AIInteraction, AIUsage, AISuggestion)
from ..auth import require_user, require_editor, require_admin, require_csrf, audit
from ..templating import templates
from ..catalogue import SOLUTIONS
from .. import ai_service as service
from ..secrets_store import (ANTHROPIC_KEY, anthropic_status, set_secret, delete_secret)

router = APIRouter(prefix="/gst8020/ai", tags=["ai"])


def _context(user, **kw):
    base = {"user": user, "solutions": SOLUTIONS, "module": "gst8020", "nav": "ai",
            "capabilities": service.CAPABILITIES}
    base.update(kw)
    return base


def _finops(db: Session, user: User):
    stmt = select(AIUsage)
    if not user.is_admin:
        stmt = stmt.where(AIUsage.user_id == user.id)
    rows = list(db.scalars(stmt.order_by(AIUsage.created_at.desc()).limit(1000)))
    month_start = dt.datetime.now(dt.timezone.utc).replace(day=1, hour=0, minute=0,
                                                            second=0, microsecond=0)
    month = [x for x in rows if x.created_at.replace(tzinfo=x.created_at.tzinfo or dt.timezone.utc) >= month_start]
    by_capability = defaultdict(lambda: {"requests": 0, "tokens": 0, "cost": 0.0, "errors": 0})
    daily = defaultdict(lambda: {"requests": 0, "tokens": 0, "cost": 0.0})
    for x in month:
        total_tokens = x.input_tokens + x.output_tokens + x.cache_creation_tokens + x.cache_read_tokens
        item = by_capability[x.capability]
        item["requests"] += 1; item["tokens"] += total_tokens; item["cost"] += x.estimated_cost_usd
        item["errors"] += 0 if x.success else 1
        day = x.created_at.strftime("%d-%b")
        daily[day]["requests"] += 1; daily[day]["tokens"] += total_tokens
        daily[day]["cost"] += x.estimated_cost_usd
    success = sum(1 for x in month if x.success)
    return {"rows": rows[:50], "month": month,
            "requests": len(month), "success_rate": (success / len(month) * 100 if month else 0),
            "tokens": sum(x.input_tokens + x.output_tokens + x.cache_creation_tokens + x.cache_read_tokens for x in month),
            "cost": sum(x.estimated_cost_usd for x in month),
            "latency": (sum(x.latency_ms for x in month) / len(month) if month else 0),
            "by_capability": dict(by_capability), "daily": dict(daily)}


@router.get("", response_class=HTMLResponse)
def workspace(request: Request, interaction: int = 0, user: User = Depends(require_user),
              db: Session = Depends(get_db)):
    cfg = service.settings(db)
    runs = list(db.scalars(select(Run).where(Run.status != "superseded")
                           .order_by(Run.period_start.desc(), Run.id.desc()).limit(36)))
    recent_stmt = select(AIInteraction).order_by(AIInteraction.created_at.desc()).limit(20)
    if not user.is_admin:
        recent_stmt = recent_stmt.where(AIInteraction.user_id == user.id)
    selected = db.get(AIInteraction, interaction) if interaction else None
    if selected and not user.is_admin and selected.user_id != user.id:
        raise HTTPException(403, "That AI result belongs to another user.")
    suggestions = list(db.scalars(select(AISuggestion).where(AISuggestion.status == "pending")
                                  .order_by(AISuggestion.created_at.desc()).limit(30)))
    result_rows = list(db.scalars(select(RunRow).order_by(RunRow.id.desc()).limit(300)))
    creditors = list(db.scalars(select(Creditor).order_by(Creditor.account_name).limit(500)))
    return templates.TemplateResponse(request, "gst/ai.html", _context(
        user, settings=cfg, key_status=anthropic_status(db), runs=runs,
        interactions=list(db.scalars(recent_stmt)), selected=selected,
        suggestions=suggestions, result_rows=result_rows, creditors=creditors,
        finops=_finops(db, user),
        flash=request.session.pop("flash", None)))


@router.post("/settings", dependencies=[Depends(require_csrf)])
def update_settings(request: Request, enabled: str = Form(""),
                    explain_classification: str = Form(""), monthly_risk_summary: str = Form(""),
                    group_rectifications: str = Form(""), results_qa: str = Form(""),
                    creditor_suggestions: str = Form(""), draft_narratives: str = Form(""),
                    model: str = Form("claude-sonnet-5"), max_tokens: int = Form(1200),
                    input_cost_per_million: float = Form(0),
                    output_cost_per_million: float = Form(0), monthly_budget_usd: float = Form(0),
                    user: User = Depends(require_admin), db: Session = Depends(get_db)):
    cfg = db.get(AISetting, 1)
    if not cfg:
        cfg = AISetting(id=1); db.add(cfg)
    before = {name: getattr(cfg, name) for name in service.CAPABILITIES} | {"enabled": cfg.enabled}
    cfg.enabled = enabled == "1"
    for name in service.CAPABILITIES:
        setattr(cfg, name, locals()[name] == "1")
    cfg.model = model.strip()[:100] or "claude-sonnet-5"
    cfg.max_tokens = min(4096, max(256, max_tokens))
    cfg.input_cost_per_million = max(0, input_cost_per_million)
    cfg.output_cost_per_million = max(0, output_cost_per_million)
    cfg.monthly_budget_usd = max(0, monthly_budget_usd)
    cfg.updated_by_id = user.id
    after = {name: getattr(cfg, name) for name in service.CAPABILITIES} | {"enabled": cfg.enabled}
    audit(db, user, "update_ai_settings", "ai_settings", 1, before=before, after=after,
          detail=f"Model {cfg.model}; API key configured={service.api_key_configured(db)}")
    db.commit()
    request.session["flash"] = "AI Workspace settings saved. No calculation rules were changed."
    return RedirectResponse("/gst8020/ai#controls", status_code=303)


@router.post("/api-key", dependencies=[Depends(require_csrf)])
def save_api_key(request: Request, api_key: str = Form(...),
                 user: User = Depends(require_admin), db: Session = Depends(get_db)):
    value = api_key.strip()
    if len(value) < 20 or any(ch.isspace() for ch in value):
        raise HTTPException(400, "Enter a complete Anthropic API key without spaces.")
    row = set_secret(db, ANTHROPIC_KEY, value, user)
    audit(db, user, "replace_anthropic_api_key", "app_secret", ANTHROPIC_KEY,
          detail=f"Encrypted key saved; fingerprint {row.fingerprint}. The key value was not logged.")
    db.commit()
    request.session["flash"] = "Anthropic API key saved securely. Its value will not be displayed again."
    return RedirectResponse("/gst8020/ai#api-key", status_code=303)


@router.post("/api-key/remove", dependencies=[Depends(require_csrf)])
def remove_api_key(request: Request, user: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    removed = delete_secret(db, ANTHROPIC_KEY)
    audit(db, user, "remove_anthropic_api_key", "app_secret", ANTHROPIC_KEY,
          detail="Encrypted UI-managed key removed. No secret value was logged.")
    db.commit()
    request.session["flash"] = ("UI-managed Anthropic key removed." if removed else
                                "No UI-managed Anthropic key was stored.")
    return RedirectResponse("/gst8020/ai#api-key", status_code=303)


@router.post("/run", dependencies=[Depends(require_csrf)])
def run_capability(request: Request, capability: str = Form(...), run_id: int = Form(0),
                   row_id: int = Form(0), creditor_id: int = Form(0),
                   question: str = Form(""), user: User = Depends(require_user),
                   db: Session = Depends(get_db)):
    if capability not in service.CAPABILITIES:
        raise HTTPException(400, "Unknown AI capability.")
    run = db.get(Run, run_id) if run_id else None
    data, refs = {}, []
    default_question = service.CAPABILITIES[capability]
    if capability == "explain_classification":
        row = db.get(RunRow, row_id)
        if not row:
            raise HTTPException(404, "Choose a valid calculated row.")
        run = db.get(Run, row.run_id)
        data, refs = service.row_context(row)
        question = question or "Explain why this exact line has its stored classification and what evidence supports it."
    elif capability == "creditor_suggestions":
        creditor = db.get(Creditor, creditor_id)
        if not creditor:
            raise HTTPException(404, "Choose a creditor to review.")
        names = [x for (x,) in db.execute(select(Creditor.account_name)) if x and x != creditor.account_name]
        matches = difflib.get_close_matches(creditor.account_name, names, n=8, cutoff=.45)
        candidates = list(db.scalars(select(Creditor).where(Creditor.account_name.in_(matches)))) if matches else []
        data = {"target": {"id": creditor.id, "account_name": creditor.account_name,
                            "legal_name": creditor.legal_name, "gstin": creditor.gstin,
                            "pan": creditor.pan, "group": creditor.group_name},
                "deterministic_name_candidates": [{"id": x.id, "account_name": x.account_name,
                    "legal_name": x.legal_name, "gstin": x.gstin, "pan": x.pan} for x in candidates]}
        refs = [f"creditor:{creditor.id}"] + [f"creditor:{x.id}" for x in candidates]
        question = question or "Rank only the supplied candidates and suggest corrections, with evidence and uncertainty."
    else:
        if not run:
            raise HTTPException(404, "Choose a calculated month.")
        data, refs = service.run_context(db, run, include_rows=(capability == "results_qa"))
        if capability == "group_rectifications":
            question = question or "Group open items by likely root cause and propose an ordered remediation plan."
        elif capability == "monthly_risk_summary":
            prior = db.scalar(select(Run).where(Run.status != "superseded",
                Run.period_start < run.period_start).order_by(Run.period_start.desc()))
            if prior:
                prior_data, prior_refs = service.run_context(db, prior)
                data["prior_month_comparison"] = prior_data
                refs.extend(prior_refs)
            question = question or "Summarise monthly movements, project concentration, compliance risks and actions."
        elif capability == "draft_narratives":
            question = question or "Draft separate management and audit-ready narratives. Distinguish facts from judgement."
        elif not question.strip():
            raise HTTPException(400, "Enter a question about the calculated results.")
    interaction = service.ask(db, user, capability, run, question or default_question, data, refs)
    audit(db, user, "run_ai_capability", "ai_interaction", interaction.id,
          after={"capability": capability, "run_id": run.id if run else None})
    db.commit()
    return RedirectResponse(f"/gst8020/ai?interaction={interaction.id}#result", status_code=303)


@router.post("/suggestions/{suggestion_id}", dependencies=[Depends(require_csrf)])
def review_suggestion(suggestion_id: int, request: Request, decision: str = Form(...),
                      note: str = Form(""), user: User = Depends(require_editor),
                      db: Session = Depends(get_db)):
    item = db.get(AISuggestion, suggestion_id)
    if not item or item.status != "pending":
        raise HTTPException(404, "That pending suggestion no longer exists.")
    if decision not in ("approved", "rejected"):
        raise HTTPException(400, "Choose approve or reject.")
    item.status, item.reviewed_by_id = decision, user.id
    item.reviewed_at = dt.datetime.now(dt.timezone.utc)
    if note.strip():
        item.rationale += f"\nHuman review note: {note.strip()[:500]}"
    audit(db, user, f"{decision}_ai_suggestion", "ai_suggestion", item.id,
          after={"decision": decision, "note": note.strip()[:500]},
          detail="Disposition only; no creditor master or calculation was changed automatically.")
    db.commit()
    request.session["flash"] = (f"Suggestion {decision}. This records the decision only; "
                                "apply verified master changes on Creditors & GSTIN.")
    return RedirectResponse("/gst8020/ai#suggestions", status_code=303)
