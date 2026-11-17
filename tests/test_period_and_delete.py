"""Selected-period export and recoverable draft deletion workflow."""
import datetime as dt
import html
import io
import os
from pathlib import Path
import re
import tempfile

import openpyxl

TEST_VAR = tempfile.mkdtemp(prefix="gst8020-period-test-")
os.environ["VAR_DIR"] = TEST_VAR
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@test.local"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "GoldenTestPassword1"

from fastapi.testclient import TestClient
from sqlalchemy import select, func

from app.main import app
from app.auth import hash_password
from app.db import SessionLocal
from app.models import Run, RunRow, AuditLog, User, Creditor
from app.routers.gst8020 import creditors_with_manual_updates, import_creditors

def add_row(db, run, amount, status):
    db.add(RunRow(
        run_id=run.id, voucher_id=f"{run.period_start}|JV|{run.id}", head="Expenses",
        year=run.financial_year, month=run.period_month, voucher_date=run.period_start,
        voucher_type="JV", voucher_no=str(run.id), bill_no="", bill_date=None,
        project="Project A", narration="", account_head="Current Assets",
        account_name="Expense", ledger=f"Vendor {run.id}", ledger_raw=f"Vendor {run.id}",
        gstin="19AADFO9095N1ZC" if status == "Registered" else "",
        debit=amount, credit=0, closing=amount, formula_key="", eligibility="Eligible",
        gst_status=status, rcm=False, party_kind="Vendor", party_source="test", flags="",
    ))


def main():
    with TestClient(app) as client:
        page = client.get("/login")
        csrf = html.unescape(re.search(r'name="csrf_token_value" value="([^"]+)"', page.text).group(1))
        assert client.post("/login", data={"email": "admin@test.local",
                                           "password": "GoldenTestPassword1",
                                           "csrf_token_value": csrf},
                           follow_redirects=False).status_code == 303
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.email == "admin@test.local"))
            db.add(Creditor(name_key="VENDOR MANUAL", account_name="Vendor Manual",
                            group_name="Sundry Creditors", gstin="19AADFO9095N1ZC",
                            gstin_valid="ok", source="manual", updated_by_id=admin.id))
            db.flush()
            effective, applied = creditors_with_manual_updates(db, [
                {"Account_Name": "Vendor Manual", "Group_Name": "Sundry Creditors",
                 "GSTIN": "", "PAN_no": "AADFO9095N"},
            ])
            assert applied == 1 and effective[0]["GSTIN"] == "19AADFO9095N1ZC"
            import_creditors(db, [{"Account_Name": "Vendor Manual",
                                   "Group_Name": "Sundry Creditors",
                                   "GSTIN": "06AABFZ1889K1ZM"}], admin)
            assert db.scalar(select(Creditor).where(
                Creditor.name_key == "VENDOR MANUAL")).gstin == "19AADFO9095N1ZC"
            preparer = User(name="Preparer", email="preparer@test.local",
                            password_hash=hash_password("PreparerPassword1"),
                            role="preparer", is_active=True)
            db.add(preparer)
            db.flush()
            april = Run(label="April draft", period_month="Apr-26", period_start=dt.date(2026, 4, 1),
                        financial_year="2026-2027", status="draft", created_by_id=preparer.id)
            may = Run(label="May frozen", period_month="May-26", period_start=dt.date(2026, 5, 1),
                      financial_year="2026-2027", status="frozen", created_by_id=admin.id,
                      frozen_at=dt.datetime.now(dt.timezone.utc))
            june = Run(label="June draft", period_month="Jun-26", period_start=dt.date(2026, 6, 1),
                       financial_year="2026-2027", status="draft", created_by_id=admin.id)
            db.add_all([april, may, june])
            db.flush()
            add_row(db, april, 100, "Unregistered")
            add_row(db, may, 400, "Registered")
            add_row(db, june, 200, "Registered")
            db.commit()
            april_id, may_id, june_id = april.id, may.id, june.id

        frozen_only = client.get("/gst8020/period?fy=2026-2027&start=2026-04&end=2026-05")
        assert frozen_only.status_code == 200
        assert "May frozen" in frozen_only.text and "April draft" not in frozen_only.text

        combined = client.get(
            "/gst8020/period/export.xlsx?fy=2026-2027&start=2026-04&end=2026-05&include_drafts=true")
        assert combined.status_code == 200
        wb = openpyxl.load_workbook(io.BytesIO(combined.content), read_only=True, data_only=True)
        assert wb["Selected Period Summary"]["B3"].value == 2
        assert wb["Selected Period Summary"]["B6"].value == 500
        assert wb["Selected Period Summary"]["B9"].value == 0.8
        assert wb["80-20 Table"].max_row == 3
        wb.close()

        client.get("/logout")
        page = client.get("/login")
        preparer_csrf = html.unescape(re.search(
            r'name="csrf_token_value" value="([^"]+)"', page.text).group(1))
        assert client.post("/login", data={"email": "preparer@test.local",
                                            "password": "PreparerPassword1",
                                            "csrf_token_value": preparer_csrf},
                           follow_redirects=False).status_code == 303
        assert "Freeze Apr-26" in client.get(f"/gst8020/runs/{april_id}").text
        assert client.post(f"/gst8020/runs/{april_id}/freeze",
                           data={"csrf_token_value": preparer_csrf},
                           follow_redirects=False).status_code == 303
        assert client.post(f"/gst8020/runs/{april_id}/delete",
                           data={"reason": "Preparer cannot delete frozen",
                                 "csrf_token_value": preparer_csrf}).status_code == 403

        client.get("/logout")
        page = client.get("/login")
        csrf = html.unescape(re.search(
            r'name="csrf_token_value" value="([^"]+)"', page.text).group(1))
        assert client.post("/login", data={"email": "admin@test.local",
                                           "password": "GoldenTestPassword1",
                                           "csrf_token_value": csrf},
                           follow_redirects=False).status_code == 303

        response = client.post(f"/gst8020/runs/{april_id}/delete",
                               data={"reason": "Administrator approved frozen removal",
                                     "csrf_token_value": csrf},
                               follow_redirects=False)
        assert response.status_code == 303
        assert client.post(f"/gst8020/runs/{june_id}/delete",
                           data={"reason": "Incorrect source export", "csrf_token_value": csrf},
                           follow_redirects=False).status_code == 303
        with SessionLocal() as db:
            assert db.get(Run, april_id).status == "superseded"
            assert db.get(Run, may_id).status == "frozen"
            assert db.get(Run, june_id).status == "superseded"
            assert db.scalar(select(func.count(AuditLog.id)).where(
                AuditLog.action == "delete_frozen_run", AuditLog.entity_id == str(april_id))) == 1
            assert db.scalar(select(func.count(AuditLog.id)).where(
                AuditLog.action == "delete_draft_run", AuditLog.entity_id == str(june_id))) == 1

    print("PASS: period export, user freeze, admin frozen deletion and draft deletion")


if __name__ == "__main__":
    main()
