"""Second-month stress test; August rows are synthetic shifts of July inputs.

This is not an Oswal August source or approved output.  It tests month-key,
classification invariance and project-wise FY aggregation without inventing
new commercial values.
"""
from pathlib import Path

from app.engine import readers
from app.engine.golden import calculate
from app.engine.calc import summarise_by_project, summarise_portfolio

REF = Path(__file__).resolve().parents[1] / "reference"


def source_rows():
    return (readers.read_daybook(REF / "DayBookRegister.xlsx"),
            readers.read_voucher(REF / "Search Voucher.xlsx"),
            readers.read_creditors(REF / "Creditors Details (1).xlsx"))


def main():
    daybook, voucher, creditors = source_rows()
    july = calculate(daybook, voucher, creditors)["rows"]
    daybook, voucher, creditors = source_rows()
    for row in daybook:
        d = readers.parse_date(row["Date"])
        row["Date"] = d.replace(month=8)
    for row in voucher:
        d = readers.parse_date(row["Voucher_Date"])
        if d:
            row["Voucher_Date"] = d.replace(month=8)
    august = calculate(daybook, voucher, creditors)["rows"]
    assert len(july) == len(august) == 1232
    for old, new in zip(july, august):
        assert new["month"] == "Aug-26" and new["year"] == "2026-2027"
        assert new["voucher_date"].month == 8
        for field in ("head", "project", "account_name", "ledger", "gstin",
                      "debit", "credit", "closing", "formula_key", "eligibility",
                      "gst_status"):
            assert old[field] == new[field], (field, old["voucher_id"])
    fy_rows = july + august
    projects = summarise_by_project(fy_rows)
    total = summarise_portfolio(fy_rows)
    assert total["gap"] > 0 and total["below_projects"] == 1
    assert abs(total["gap"] - sum(p["gap"] for p in projects.values())) < .00001
    assert abs(total["exposure"] - sum(p["exposure"] for p in projects.values())) < .00001
    assert abs(total["gap"] - 481528.216) < .001, total["gap"]
    print("PASS: synthetic August keeps 1,232 updated classifications; July+August FY project gap reconciles")


if __name__ == "__main__":
    main()
