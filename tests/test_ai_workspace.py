"""No-network checks for AI controls, grounding, approvals and FinOps capture."""
import datetime as dt

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import User, Run, RunRow, AISetting, AIUsage, AIInteraction, AISuggestion
from app import ai_service
from app.routers.ai import update_settings
from app.secrets_store import (ANTHROPIC_KEY, set_secret, get_secret, delete_secret,
                               anthropic_status)
from app.models import AppSecret
from starlette.requests import Request


def main():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine)
    with Session() as db:
        user = User(email="ai@test.local", name="AI Tester", password_hash="x", role="admin")
        db.add(user); db.flush()
        run = Run(label="August", period_month="Aug-26", period_start=dt.date(2026, 8, 1),
                  financial_year="2026-2027", created_by_id=user.id, stats="{}")
        db.add(run); db.flush()
        row = RunRow(run_id=run.id, voucher_id="2026-08-10|JV|42", head="Expenses",
            year="2026-2027", month="Aug-26", voucher_date=dt.date(2026, 8, 10),
            voucher_type="JV", voucher_no="42", project="Project A", account_name="Rent",
            account_head="Expense", ledger="Vendor One", ledger_raw="Vendor One",
            gstin="19AADFO9095N1ZC", debit=1000, closing=1000, eligibility="Eligible",
            gst_status="Registered", rcm=False, party_source="creditors_exact", flags="")
        db.add(row)
        db.add(AISetting(id=1, enabled=True, explain_classification=True,
                         creditor_suggestions=True, model="test-claude", max_tokens=500,
                         input_cost_per_million=2, output_cost_per_million=10))
        db.commit()

        context, refs = ai_service.row_context(row)
        assert context["voucher_id"] == "2026-08-10|JV|42"
        assert context["ledger"] == "Vendor One" and context["gstin"] == "19AADFO9095N1ZC"
        assert "creditor match" in context["applied_rule"]

        captured = {}
        def transport(payload):
            captured.update(payload)
            return ({"content": [{"type": "text", "text": "Grounded explanation"}],
                     "usage": {"input_tokens": 100, "output_tokens": 20,
                               "cache_creation_input_tokens": 5, "cache_read_input_tokens": 10}},
                    "req_test_123")
        interaction = ai_service.ask(db, user, "explain_classification", run,
                                     "Explain this row", context, refs, transport=transport)
        assert interaction.response == "Grounded explanation"
        assert "2026-08-10|JV|42" in captured["messages"][0]["content"]
        usage = db.scalar(select(AIUsage))
        assert usage.success and usage.request_id == "req_test_123"
        assert usage.input_tokens == 100 and usage.output_tokens == 20
        assert round(usage.estimated_cost_usd, 7) == round((115 * 2 + 20 * 10) / 1_000_000, 7)
        assert db.scalar(select(AIInteraction)).source_refs == '["run_row:1", "voucher:2026-08-10|JV|42"]'
        assert db.scalar(select(AISuggestion)) is None

        cfg = db.get(AISetting, 1)
        cfg.explain_classification = False
        db.commit()
        try:
            ai_service.ask(db, user, "explain_classification", run, "x", context, refs,
                           transport=transport)
            raise AssertionError("disabled capability should not run")
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 403

        request = Request({"type": "http", "method": "POST", "path": "/gst8020/ai/settings",
                           "headers": [], "query_string": b"", "server": ("test", 80),
                           "scheme": "http", "session": {}})
        response = update_settings(request, enabled="1", explain_classification="1",
            monthly_risk_summary="1", group_rectifications="", results_qa="1",
            creditor_suggestions="", draft_narratives="1", model="test-claude-2",
            max_tokens=800, input_cost_per_million=3, output_cost_per_million=12,
            monthly_budget_usd=25, user=user, db=db)
        assert response.status_code == 303
        cfg = db.get(AISetting, 1)
        assert cfg.enabled and cfg.monthly_risk_summary and not cfg.group_rectifications
        assert cfg.model == "test-claude-2" and cfg.monthly_budget_usd == 25

        secret = set_secret(db, ANTHROPIC_KEY, "sk-ant-test-secret-value-123456", user)
        db.commit()
        assert get_secret(db, ANTHROPIC_KEY) == "sk-ant-test-secret-value-123456"
        stored = db.get(AppSecret, ANTHROPIC_KEY)
        assert "sk-ant-test" not in stored.encrypted_value
        assert anthropic_status(db)["source"] == "Admin UI"
        assert secret.fingerprint.startswith("sha256:")
        assert delete_secret(db, ANTHROPIC_KEY)
        db.commit()
        assert get_secret(db, ANTHROPIC_KEY) == ""
    print("PASS: AI grounding, default controls and FinOps telemetry")


if __name__ == "__main__":
    main()
