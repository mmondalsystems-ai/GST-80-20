"""Small rule tests for the single approved calculation entry point."""
from datetime import date
from pathlib import Path
import sys
import io
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.calc import calculate as public_calculate, summarise, report_table
from app.engine.golden import calculate as golden_calculate, INELIGIBLE_KEYWORDS
from app.engine.readers import read_creditors


def main():
    assert public_calculate is golden_calculate, "A second calculator is active"
    d = date(2026, 7, 1)
    daybook = [{
        "Date": d, "Voucher_Type": "Journal", "Voucher_No": 1,
        "Particulars": "CGST expense", "Fixed_Group_Name": "Current Assets",
        "Parent_Group_Name": "Other", "Debit": 100.0, "Credit": 0.0,
        "Bill_No": "", "Bill_Date": None, "Cost_Centre": "Project A",
        "Narration": "Source-only narration",
    }]
    voucher = [
        {"Voucher_Date": d, "Voucher_Type": "Journal", "Voucher_No": 1,
         "Particulars": "Vendor A", "Debit_Amount": 0.0,
         "Credit_Amount": 100.0, "Status": "Approved"},
        {"Voucher_Date": d, "Voucher_Type": "Journal", "Voucher_No": 1,
         "Particulars": "RCM payable", "Debit_Amount": 0.0,
         "Credit_Amount": 0.0, "Status": "Approved"},
    ]
    output_rcm_ledgers = (
        "Output CGST on RCM", "Output SGST on RCM", "Output IGST on RCM",
    )
    for voucher_no, rcm_ledger in enumerate(output_rcm_ledgers, 2):
        daybook.extend([
            {
                "Date": d, "Voucher_Type": "Journal", "Voucher_No": voucher_no,
                "Particulars": "CGST professional expense" if voucher_no == 2 else "Professional expense",
                "Fixed_Group_Name": "Current Assets", "Parent_Group_Name": "Other",
                "Debit": 100.0, "Credit": 0.0, "Bill_No": "", "Bill_Date": None,
                "Cost_Centre": "Project A", "Narration": "",
            },
            {
                "Date": d, "Voucher_Type": "Journal", "Voucher_No": voucher_no,
                "Particulars": rcm_ledger, "Fixed_Group_Name": "CGST",
                "Parent_Group_Name": "Duties & Taxes", "Debit": 18.0, "Credit": 0.0,
                "Bill_No": "", "Bill_Date": None, "Cost_Centre": "Project A",
                "Narration": "",
            },
        ])
        voucher.append({
            "Voucher_Date": d, "Voucher_Type": "Journal", "Voucher_No": voucher_no,
            "Particulars": f"Vendor {voucher_no}", "Debit_Amount": 0.0,
            "Credit_Amount": 118.0, "Status": "Approved",
        })
    # Ordinary GST ledgers are now business-approved evidence of registration,
    # even when the creditor export has no GSTIN.
    daybook.extend([
        {"Date": d, "Voucher_Type": "Journal", "Voucher_No": 5,
         "Particulars": "Professional expense", "Fixed_Group_Name": "Current Assets",
         "Parent_Group_Name": "Other", "Debit": 100.0, "Credit": 0.0,
         "Bill_No": "", "Bill_Date": None, "Cost_Centre": "Project A", "Narration": ""},
        {"Date": d, "Voucher_Type": "Journal", "Voucher_No": 5,
         "Particulars": "Input CGST @ 9%", "Fixed_Group_Name": "CGST",
         "Parent_Group_Name": "Duties & Taxes", "Debit": 9.0, "Credit": 0.0,
         "Bill_No": "", "Bill_Date": None, "Cost_Centre": "Project A", "Narration": ""},
    ])
    voucher.append({"Voucher_Date": d, "Voucher_Type": "Journal", "Voucher_No": 5,
                    "Particulars": "Vendor 5", "Debit_Amount": 0.0,
                    "Credit_Amount": 109.0, "Status": "Approved"})
    creditors = [
        {"Account_Name": "Vendor A", "Group_Name": "Sundry Creditors", "GSTIN": "INVALID"},
        *({"Account_Name": f"Vendor {voucher_no}", "Group_Name": "Sundry Creditors", "GSTIN": ""}
          for voucher_no in range(2, 6)),
    ]
    result = public_calculate(daybook, voucher, creditors, {
        "include_fixed_groups": ["Current Assets"],
        "exclude_parent_groups": [],
        # Stored settings must not supersede the approved Python constants.
        "ineligible_keywords": [],
    })
    assert "CGST" in INELIGIBLE_KEYWORDS
    assert len(result["rows"]) == 5
    row = result["rows"][0]
    assert row["formula_key"] == "CGST"
    assert row["eligibility"] == "Ineligible"  # RCM does not override the keyword
    assert row["gst_status"] == "Registered"  # populated GST No. is enough
    assert row["rcm"] is True
    assert row["narration"] == ""  # the approved workbook intentionally leaves it blank
    assert row["source_narration"] == "Source-only narration"
    output_rcm_rows = result["rows"][1:4]
    assert all(item["eligibility"] == "Eligible" for item in output_rcm_rows)
    assert all(item["gst_status"] == "Registered" for item in output_rcm_rows)
    assert all(item["gstin"] == "" for item in output_rcm_rows)
    assert all(item["rcm"] is True for item in output_rcm_rows)
    output_rcm_ids = {item["voucher_id"] for item in output_rcm_rows}
    assert not output_rcm_ids.intersection(
        item["voucher_id"] for item in result["rectifications"]
    )
    normal_gst_row = result["rows"][4]
    assert normal_gst_row["gst_status"] == "Registered"
    assert normal_gst_row["gstin"] == ""
    assert normal_gst_row["eligibility"] == "Eligible"
    assert any(item["voucher_id"] == normal_gst_row["voucher_id"] and
               "no GSTIN" in item["reason"] for item in result["rectifications"])

    # The replacement registration export headings are accepted and mapped to
    # the legacy calculator fields without modifying the source workbook.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Company_Name", "Fixed_Group", "Parent_Group", "Account_Name",
               "PAN_No", "State_Name", "State_Code", "Reg_No"])
    ws.append(["Company", "Sundry Creditors", "Sundry Creditors", "Vendor X",
               "ABCDE1234F", "West Bengal", "19", "19ABCDE1234F1Z5"])
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    mapped = read_creditors(stream)
    assert mapped[0]["Group_Name"] == "Sundry Creditors"
    assert mapped[0]["GSTIN"] == "19ABCDE1234F1Z5"
    assert mapped[0]["PAN_no"] == "ABCDE1234F"
    assert mapped[0]["State_Code"] == "19"

    # A blank-GSTIN creditor in the replacement mixed export is still the
    # concerned party in a bank-payment voucher; the bank must not win merely
    # because it is the credit-side line.
    bank_daybook = [
        {"Date": d, "Voucher_Type": "BP", "Voucher_No": 20,
         "Particulars": "Rounding expense", "Fixed_Group_Name": "Current Assets",
         "Parent_Group_Name": "Other", "Debit": 0.5, "Credit": 0.0,
         "Bill_No": "", "Bill_Date": None, "Cost_Centre": "Project A", "Narration": ""},
        {"Date": d, "Voucher_Type": "BP", "Voucher_No": 20,
         "Particulars": "Vendor Without GSTIN", "Fixed_Group_Name": "Sundry Creditors",
         "Parent_Group_Name": "Current Liabilities", "Debit": 100.0, "Credit": 0.0,
         "Bill_No": "", "Bill_Date": None, "Cost_Centre": "Project A", "Narration": ""},
        {"Date": d, "Voucher_Type": "BP", "Voucher_No": 20,
         "Particulars": "Axis Bank - 1234", "Fixed_Group_Name": "Bank Accounts",
         "Parent_Group_Name": "Current Assets", "Debit": 0.0, "Credit": 100.5,
         "Bill_No": "", "Bill_Date": None, "Cost_Centre": "Project A", "Narration": ""},
    ]
    bank_voucher = [
        {"Voucher_Date": d, "Voucher_Type": "BP", "Voucher_No": 20,
         "Particulars": "Rounding expense", "Debit_Amount": 0.5,
         "Credit_Amount": 0.0, "Status": "Approved"},
        {"Voucher_Date": d, "Voucher_Type": "BP", "Voucher_No": 20,
         "Particulars": "Vendor Without GSTIN", "Debit_Amount": 100.0,
         "Credit_Amount": 0.0, "Status": "Approved"},
        {"Voucher_Date": d, "Voucher_Type": "BP", "Voucher_No": 20,
         "Particulars": "Axis Bank - 1234", "Debit_Amount": 0.0,
         "Credit_Amount": 100.5, "Status": "Approved"},
    ]
    bank_creditors = [{"Company_Name": "Company", "Account_Name": "Vendor Without GSTIN",
                       "Group_Name": "Sundry Creditors", "GSTIN": "", "State_Code": ""}]
    bank_result = public_calculate(bank_daybook, bank_voucher, bank_creditors, {
        "include_fixed_groups": ["Current Assets"], "exclude_parent_groups": [],
        "creditor_group_allowlist": ["Sundry Creditors"],
    })
    assert bank_result["rows"][0]["ledger"] == "Vendor Without GSTIN"
    assert bank_result["rows"][0]["gst_status"] == "Unregistered"

    # Multiple registrations are selected from voucher tax evidence, never by
    # workbook row order. An ambiguous no-tax voucher stays unresolved.
    multi_daybook, multi_voucher = [], []
    for number, tax_kind in ((10, "CGST"), (11, "IGST"), (12, "")):
        multi_daybook.append({
            "Date": d, "Voucher_Type": "Journal", "Voucher_No": number,
            "Particulars": "Professional expense", "Fixed_Group_Name": "Current Assets",
            "Parent_Group_Name": "Other", "Debit": 100.0, "Credit": 0.0,
            "Bill_No": "", "Bill_Date": None, "Cost_Centre": "Project A", "Narration": "",
        })
        if tax_kind:
            multi_daybook.append({
                "Date": d, "Voucher_Type": "Journal", "Voucher_No": number,
                "Particulars": f"Input {tax_kind}", "Fixed_Group_Name": tax_kind,
                "Parent_Group_Name": "Duties & Taxes", "Debit": 18.0, "Credit": 0.0,
                "Bill_No": "", "Bill_Date": None, "Cost_Centre": "Project A", "Narration": "",
            })
        multi_voucher.append({
            "Voucher_Date": d, "Voucher_Type": "Journal", "Voucher_No": number,
            "Particulars": "Multi State Vendor", "Debit_Amount": 0.0,
            "Credit_Amount": 118.0 if tax_kind else 100.0, "Status": "Approved",
        })
    multi_creditors = [
        {"Account_Name": "Multi State Vendor", "Group_Name": "Sundry Creditors",
         "GSTIN": "06AABFZ1889K1ZM", "State_Code": "06", "Company_Name": "Company"},
        {"Account_Name": "Multi State Vendor", "Group_Name": "Sundry Creditors",
         "GSTIN": "19AADFO9095N1ZC", "State_Code": "19", "Company_Name": "Company"},
    ]
    multi = public_calculate(multi_daybook, multi_voucher, multi_creditors, {
        "include_fixed_groups": ["Current Assets"], "exclude_parent_groups": [],
        "creditor_group_allowlist": ["Sundry Creditors"], "company_state_code": "19",
    })
    by_number = {str(row["voucher_no"]): row for row in multi["rows"]}
    assert by_number["10"]["gstin"] == "19AADFO9095N1ZC"
    assert by_number["11"]["gstin"] == "06AABFZ1889K1ZM"
    assert by_number["12"]["gstin"] == "" and by_number["12"]["gst_status"] == "Unregistered"
    assert "ambiguous_gstin" in by_number["12"]["flags"]
    assert any("Multiple GSTINs" in item["reason"] for item in multi["rectifications"])
    # Report!H2 applies ABS to each net GST bucket, not to the signed total.
    negative_mix = summarise([
        {"head": "Expenses", "eligibility": "Eligible", "gst_status": "Registered",
         "closing": -100.0},
        {"head": "Expenses", "eligibility": "Eligible", "gst_status": "Unregistered",
         "closing": 25.0},
    ])
    assert negative_mix["pct"] == 80.0
    report = report_table([row])
    assert [(line["elig"], line["status"]) for line in report] == [
        ("Eligible", "Registered"), ("Eligible", "Unregistered"),
        ("Eligible", "Eligible total"),
        ("Ineligible", "Registered"), ("Ineligible", "Unregistered"),
        ("Ineligible", "Ineligible total"),
    ]
    print("PASS: legacy and replacement masters plus GST-ledger registration rules")


if __name__ == "__main__":
    main()
