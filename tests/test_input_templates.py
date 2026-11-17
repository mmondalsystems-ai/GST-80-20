"""Downloadable inputs match the supplied Tally layouts and reader contract."""
from pathlib import Path

import openpyxl

from app.engine import readers


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "static" / "input-templates"
REFERENCE = ROOT / "reference"

SPECS = (
    ("Day_Book_Register_Template.xlsx", "DayBookRegister.xlsx", readers.DAYBOOK_COLS),
    ("Search_Voucher_Template.xlsx", "Search Voucher.xlsx", readers.VOUCHER_COLS),
    ("Creditors_Details_Template.xlsx", "Creditors Details (1).xlsx", readers.CREDITOR_COLS),
)


def _headings(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        for row in ws.iter_rows(min_row=1, max_row=60, values_only=True):
            values = [readers.norm(value) for value in row]
            if len([v for v in values if v]) >= 3:
                return values
    finally:
        wb.close()
    raise AssertionError(f"No headings in {path}")


def main():
    for template_name, source_name, required in SPECS:
        path = TEMPLATES / template_name
        assert path.is_file(), path
        headings = _headings(path)
        present = {readers.key(v) for v in headings}
        assert all(name in present for name in required)
        if (REFERENCE / source_name).is_file():
            assert headings == _headings(REFERENCE / source_name), template_name
        try:
            readers.read_sheet(path, required, template_name)
            raise AssertionError("Blank template was accepted as input")
        except readers.IngestError as exc:
            assert "no data rows" in str(exc)

    if (REFERENCE / "Search Voucher.xlsx").is_file():
        try:
            readers.read_daybook(REFERENCE / "Search Voucher.xlsx")
            raise AssertionError("Search Voucher was accepted as Day Book")
        except readers.IngestError as exc:
            assert "appears to be a Search Voucher" in str(exc)
    print("PASS: three exact blank Tally templates; wrong file and empty data rejected")


if __name__ == "__main__":
    main()
