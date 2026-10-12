#!/usr/bin/env python3
"""Load the sample month so the portal opens with figures already in it.

Reads the three Tally exports in demo-inputs/ and stores the result exactly as
an upload through the web page would, so the demo starts on a populated
dashboard rather than an empty one.

Safe to run more than once: an earlier run of the same month is superseded, not
duplicated, which is the same behaviour as re-uploading through the page.

    python seed_demo.py
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from sqlalchemy import select, func                                    # noqa: E402
from app.db import Base, engine, SessionLocal                          # noqa: E402
from app.models import User, Run, RunRow, Rectification, Creditor      # noqa: E402
from app.auth import audit                                             # noqa: E402
from app.main import startup                                           # noqa: E402
from app.engine import readers as R                                    # noqa: E402
from app.engine.golden import calculate                            # noqa: E402
from app.engine.calc import summarise_portfolio, summarise_by_project # noqa: E402
from app.routers.gst8020 import import_creditors, load_config, RESOLVED_STATUSES  # noqa: E402
from app.license import evaluate as evaluate_license            # noqa: E402
from app.backup import backup_after_run                           # noqa: E402

INPUTS = BASE / "demo-inputs"
FILES = {
    "daybook": "DayBookRegister.xlsx",
    "voucher": "Search Voucher.xlsx",
    "creditors": "Creditors Details.xlsx",
}


def main():
    missing = [n for n in FILES.values() if not (INPUTS / n).exists()]
    if missing:
        print(f"Missing from {INPUTS.name}/: {', '.join(missing)}", file=sys.stderr)
        return 1

    startup()                                    # creates the tables and the first user
    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        licence = evaluate_license(db)
        if licence.read_only:
            print(f"Demo seed stopped: {licence.message}", file=sys.stderr)
            return 1
        user = db.scalar(select(User).order_by(User.id))
        cfg = load_config(db)

        try:
            daybook = R.read_daybook(INPUTS / FILES["daybook"])
            voucher = R.read_voucher(INPUTS / FILES["voucher"])
            creditors_raw = R.read_creditors(INPUTS / FILES["creditors"])
        except R.IngestError as e:
            print(str(e), file=sys.stderr)
            return 1

        # Seed through the exact same calculation inputs as a web upload.
        # The shared creditor master is for follow-up, never a substitute for
        # this run's supplied export.
        result = calculate(daybook, voucher, creditors_raw, cfg)
        rows = result["rows"]
        if not rows:
            print("No reportable lines came out of these files.", file=sys.stderr)
            return 1

        period = rows[0]["month"]
        fy = rows[0]["year"]
        first_date = min(r["voucher_date"] for r in rows)

        if db.scalar(select(Run).where(Run.period_month == period,
                                        Run.financial_year == fy,
                                        Run.status == "frozen")):
            print(f"{period} is frozen; the demo seed will not replace it.", file=sys.stderr)
            return 1
        import_creditors(db, creditors_raw, user)
        for old in db.scalars(select(Run).where(Run.period_month == period,
                                                Run.financial_year == fy,
                                                Run.status == "draft")):
            old.status = "superseded"
        db.flush()

        stats = summarise_portfolio(rows, cfg)
        stats["by_project"] = summarise_by_project(rows, cfg)
        run = Run(label=f"{period} — sample month", period_month=period,
                  period_start=first_date.replace(day=1), financial_year=fy,
                  created_by_id=user.id,
                  source_files=json.dumps(FILES),
                  config_snapshot=json.dumps(cfg),
                  stats=json.dumps(stats, default=str))
        db.add(run)
        db.flush()

        for r in rows:
            db.add(RunRow(
                run_id=run.id, voucher_id=r["voucher_id"], head=r["head"], year=r["year"],
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

        for item in result["rectifications"]:
            db.add(Rectification(
                run_id=run.id, voucher_id=item["voucher_id"], ledger=item["ledger"][:200],
                account_name=item["account_name"][:200], project=item["project"][:120],
                amount=item["amount"], reason=item["reason"],
                suggestions=item.get("suggestions", ""), status="open"))

        audit(db, user, "create_run", "run", run.id,
              after={"period": period, "rows": len(rows), "pct": round(stats["pct"], 2)},
              detail="loaded by seed_demo.py from demo-inputs/")
        db.commit()

        open_items = db.scalar(select(func.count(Rectification.id))
                               .where(Rectification.run_id == run.id)) or 0

    print(f"    {period}: {len(rows):,} reportable lines, "
          f"{stats['pct']:.2f}% registered, {open_items} open rectifications")
    try:
        _, message = backup_after_run()
        print(f"    {message}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"    WARNING: calculation saved but daily backup failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
