#!/usr/bin/env python3
"""
Oswal Group - 80:20 GST Monthly Table Builder
================================================
Reads your three Tally exports (Day Book, Search Voucher, Creditors List) for one
month and produces the "80-20 Table" + "Report" workbook described in the
functional spec, fully computed - no manual work needed except reviewing the
"Needs Review" tab for any near-duplicate creditor names or missing GSTINs.

HOW TO RUN
----------
    pip install openpyxl
    python3 build_8020_table.py

It will open a file-selection window (one at a time) for:
  1. Your Day Book export        (e.g. DayBookRegister.xlsx)
  2. Your Search Voucher export  (e.g. Search_Voucher.xlsx)
  3. Your Creditors List export  (e.g. Creditors_Details.xlsx)

You can also pass the three paths directly as command-line arguments to skip
the prompts:
    python3 build_8020_table.py DayBookRegister.xlsx Search_Voucher.xlsx Creditors_Details.xlsx

It will then ask you which Fixed_Group_Name(s) to include (this is the "which
groups count as cost/expense" filter) and which Parent_Group_Name(s) to
exclude from within that selection - a sensible default is suggested each
time, just press Enter to accept it, or type your own comma-separated choice.

The output workbook is saved next to your Day Book file, named
"80-20_Table_<timestamp>.xlsx".

EDITABLE MASTERS (change these lists to match your own data / policy)
-----------------------------------------------------------------------
"""

import os
import sys
import re
import difflib
from datetime import datetime, date

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

# --------------------------------------------------------------------------
# EDITABLE MASTER 1: keywords that make an expense line "Ineligible"
# (matched case-insensitively as a substring against Account_Name ONLY)
# --------------------------------------------------------------------------
INELIGIBLE_KEYWORDS = [
    "CGST", "SGST", "IGST", "ESIC", "EPFO", "INTEREST", "SALARY",
    "DEPRECIATION", "DEPRECEATION",   # both spellings, in case Tally has the misspelling
    "P TAX", "PROPERTY TAX",
    "BATCHING PLANT (DISEL)", "BATCHING PLANT (DIESEL)",
]

# --------------------------------------------------------------------------
# REPORT HEAD: every included transaction is classified as "Expenses".
# --------------------------------------------------------------------------
DEFAULT_HEAD = "Expenses"

# This resolved Account Ledger is always Ineligible in the 80-20 calculation.
ALWAYS_INELIGIBLE_ACCOUNT_LEDGERS = {
    "AMA FUELS & TECHNOLOGIES PVT LTD",
    "Pugalia Automobiles",
}

# Verified corrections from the approved Sheet1 reference/master data.
# These cover creditor records whose exported GSTIN is blank or incorrect.
PARTY_MASTER_OVERRIDES = {
    "Oswal Towers LLP": ("Oswal Towers LLP", "19AADFO9095N1ZC"),
    "Oswal Imprints": ("Oswal Imprints", "19AAFFO5761M2ZM"),
    "AMA FUELS & TECHNOLOGIES PVT LTD": (
        "AMA FUELS & TECHNOLOGIES PVT LTD", "19AATCA6093M1ZQ"
    ),
    "Bajaj Housing Finance Limited (Loan)": (
        "Bajaj Housing Finance Limited (Loan)", ""
    ),
}

# --------------------------------------------------------------------------
# Fixed_Group_Name values that are NEVER cost/expense lines (settlement /
# duty / statutory groups) - used only to suggest a sensible default
# selection; you can always override the suggestion when the script asks.
# --------------------------------------------------------------------------
NEVER_COST_GROUPS = {
    "Sundry Creditors", "Sundry Debtors", "Cash Accounts", "Bank Accounts",
    "CGST", "SGST", "IGST", "TDS", "TCS", "Income (Revenue)", "Sales Accounts",
    "Advance from Flatholders", "Secured Loans", "Unsecured Loans",
    "Current Liabilities", "Duties & Taxes", "Deposits (Assets)",
    "Investments", "Expenditure Accounts",
    # "Loans & Advances (Assets)" is where staff imprest advances live. The DEBIT
    # side (giving the advance) is a balance-sheet advance, not a cost; the
    # CREDIT side (settling it against a real expense, e.g. JV 754 - Bikash
    # Haldar's diesel advance) is a settlement line, same role as Cash/Bank -
    # the actual cost already shows up as its own "Current Assets" row in the
    # same voucher. Including this group would double-count that voucher.
    "Loans & Advances (Assets)",
}

# Parent_Group_Name values that look like they're under a cost group but are
# actually NOT procurement costs (e.g. a customer receivable). Used only to
# pre-tick suggested exclusions; you can always override.
NEVER_COST_PARENT_GROUPS = {
    "TDS Receivable",
}

# Ledger-name substrings that mark a line as tax/duty/RCM settlement (used to
# find the real "party" line, and to detect Reverse Charge)
TAX_DUTY_FIXED_GROUPS = {"CGST", "SGST", "IGST", "TDS", "TCS"}
RCM_MARKER = "RCM"
GST_RCM_VALUE = "GST-RCMechanism"
OUTPUT_RCM_LEDGERS = {
    "OUTPUT CGST ON RCM",
    "OUTPUT SGST ON RCM",
    "OUTPUT IGST ON RCM",
}


# ==========================================================================
# Helpers: reading the three Tally exports
# ==========================================================================

def find_header_row(ws, required_cols, max_scan=40):
    """Scan the top of the sheet for the row that contains every column in
    required_cols (Tally exports have several title rows above the real
    header, and the exact row number can drift month to month)."""
    for r in range(1, min(max_scan, ws.max_row) + 1):
        row_vals = []
        for c in range(1, ws.max_column + 1):
            v = ws.cell(r, c).value
            row_vals.append(str(v).strip() if v is not None else "")
        if all(req in row_vals for req in required_cols):
            return r, row_vals
    raise ValueError(
        f"Could not find a header row containing all of {required_cols} in the "
        f"first {max_scan} rows of '{ws.title}'. Is this the right file?"
    )


def load_rows(path, required_cols, sheet_index=0):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[sheet_index]]
    hdr_row, headers = find_header_row(ws, required_cols)
    col_of = {h: i + 1 for i, h in enumerate(headers) if h}
    rows = []
    for r in range(hdr_row + 1, ws.max_row + 1):
        vals = {h: ws.cell(r, col_of[h]).value for h in col_of}
        if all(v is None or str(v).strip() == "" for v in vals.values()):
            continue
        rows.append(vals)
    return rows


def parse_tally_date(v):
    """Tally date exports usually come through as text like '07 Jul 2026',
    but some Excel setups store them as real date/datetime cells - handle
    both."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Could not parse date value: {v!r}")


def norm_name(s):
    if s is None:
        return ""
    value = re.sub(r"\s+", " ", str(s).strip()).lower()
    # Tally often adds presentation-only prefixes/suffixes to the same ledger.
    # Removing them lets names such as "M/S JYOTI UDYOG" match "JYOTI UDYOG"
    # and "Oswal Imprints (Creditors)" match the Creditors List master.
    value = re.sub(r"^m\s*/?\s*s[,.]?\s+", "", value)
    value = re.sub(r"\s*[-(]?\s*creditors?\s*\)?\s*$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_party_display(s):
    """Remove Tally presentation prefixes/suffixes from the output name."""
    value = re.sub(r"\s+", " ", str(s or "").strip())
    value = re.sub(r"^m\s*/?\s*s[,.]?\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\s*[-(]?\s*creditors?\s*\)?\s*$", "", value,
        flags=re.IGNORECASE,
    )
    return value.strip()


def voucher_key(d, vtype, vno):
    """Unique_Voucher_ID, built from a parsed date so it's immune to any
    text-formatting difference between the Day Book and Search Voucher
    exports."""
    vno_str = str(int(vno)) if isinstance(vno, (int, float)) and float(vno).is_integer() else str(vno).strip()
    return f"{d.isoformat()}|{str(vtype).strip().upper()}|{vno_str}"


def financial_year(d):
    if d.month >= 4:
        return f"{d.year}-{d.year + 1}"
    return f"{d.year - 1}-{d.year}"


def is_tax_duty_line(fixed_group, particulars):
    if fixed_group in TAX_DUTY_FIXED_GROUPS:
        return True
    if RCM_MARKER.upper() in (particulars or "").upper():
        return True
    return False


def matched_keyword(text):
    up = (text or "").upper()
    for kw in INELIGIBLE_KEYWORDS:
        if kw.upper() in up:
            return kw
    return ""


def is_output_rcm_ledger(value):
    """Return whether a Day Book ledger invokes the business RCM override."""
    normalized = re.sub(r"\s+", " ", str(value or "").strip()).upper()
    return normalized in OUTPUT_RCM_LEDGERS


# ==========================================================================
# Interactive prompts
# ==========================================================================

def _clean_path(p):
    p = p.strip().strip('"').strip("'")
    # a pasted Windows path sometimes carries a trailing slash/backslash if it
    # was copied from the address bar - strip it before checking
    p = p.rstrip("\\/")
    return p


def _pick_file_from_folder(folder):
    """The user gave us a folder instead of a file. List the spreadsheet
    files in it (skipping Excel's own '~$...' lock files) and let them pick
    one by number, or type a filename to look for inside that folder."""
    try:
        entries = sorted(
            f for f in os.listdir(folder)
            if f.lower().endswith((".xlsx", ".xls", ".xlsm")) and not f.startswith("~$")
        )
    except OSError as e:
        print(f"  Couldn't read that folder: {e}")
        return None
    if not entries:
        print(f"  That folder has no .xlsx/.xls files in it: {folder}")
        return None
    print(f"  That's a folder. Files found in it:")
    for i, f in enumerate(entries, start=1):
        print(f"    {i}. {f}")
    raw = input("  Type a number, or type the filename to use: ").strip().strip('"').strip("'")
    if raw.isdigit() and 1 <= int(raw) <= len(entries):
        return os.path.join(folder, entries[int(raw) - 1])
    candidate = os.path.join(folder, raw)
    if os.path.isfile(candidate):
        return candidate
    print(f"  Didn't recognise {raw!r} as one of the numbers or filenames above.")
    return None


def _pick_file_dialog(prompt):
    """Open a standard file-selection window when Tkinter is available.

    Returning None keeps the original typed-path prompt as a fallback for
    systems where Tkinter is unavailable or when the window is cancelled.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(
            "80-20 GST Table Builder",
            f"{prompt}\n\nClick OK, then select the correct Excel file.",
            parent=root,
        )
        selected = filedialog.askopenfilename(
            parent=root,
            title=prompt,
            filetypes=[
                ("Excel workbooks", "*.xlsx *.xlsm *.xls"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
        return selected or None
    except Exception as exc:
        print(f"  File-selection window unavailable ({exc}); please type the path.")
        return None


def ask_path(prompt, given=None):
    if given:
        given = _clean_path(given)
        if os.path.isfile(given):
            print(f"{prompt}: {given}")
            return given
        if os.path.isdir(given):
            picked = _pick_file_from_folder(given)
            if picked:
                return picked
        # fall through to interactive prompting if the given arg didn't resolve
        print(f"  Can't find a file at: {given!r} - falling back to asking for it.")
    else:
        picked = _pick_file_dialog(prompt)
        if picked:
            print(f"{prompt}: {picked}")
            return picked
    while True:
        raw = input(f"{prompt}: ")
        p = _clean_path(raw)
        if os.path.isfile(p):
            return p
        if os.path.isdir(p):
            picked = _pick_file_from_folder(p)
            if picked:
                return picked
            continue
        print(
            f"  Can't find a file OR folder at: {p!r}\n"
            f"  Tip: in File Explorer, right-click the file itself (not the folder) and "
            f"'Copy as path', then paste that here."
        )


def ask_selection(all_values_with_counts, suggested_default, question):
    """Print a numbered list, let the user type comma-separated numbers, or
    press Enter to accept the suggested default."""
    print(f"\n{question}")
    values = list(all_values_with_counts.keys())
    for i, v in enumerate(values, start=1):
        mark = "*" if v in suggested_default else " "
        print(f"  [{mark}] {i:>2}. {v}  ({all_values_with_counts[v]} lines)")
    print("  (* = suggested default)")
    raw = input(
        "Type the numbers to select, comma-separated, or press Enter to accept the suggested (*) set: "
    ).strip()
    if raw == "":
        return set(suggested_default)
    chosen = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            idx = int(part)
            chosen.add(values[idx - 1])
        except (ValueError, IndexError):
            print(f"  Ignoring unrecognised entry: {part!r}")
    return chosen


# ==========================================================================
# Main
# ==========================================================================

def main():
    print("=" * 78)
    print("Oswal Group - 80:20 GST Monthly Table Builder")
    print("=" * 78)

    args = sys.argv[1:]
    daybook_path = ask_path("Select file 1 of 3 - Day Book export", args[0] if len(args) > 0 else None)
    searchvoucher_path = ask_path("Select file 2 of 3 - Search Voucher export", args[1] if len(args) > 1 else None)
    creditors_path = ask_path("Select file 3 of 3 - Creditors List export", args[2] if len(args) > 2 else None)

    print("\nReading files...")
    daybook_rows = load_rows(
        daybook_path,
        ["Date", "Voucher_Type", "Voucher_No", "Particulars", "Fixed_Group_Name",
         "Parent_Group_Name", "Debit", "Credit", "Bill_No", "Bill_Date", "Cost_Centre", "Narration"],
    )
    sv_rows = load_rows(
        searchvoucher_path,
        ["Voucher_Date", "Voucher_No", "Voucher_Type", "Particulars",
         "Debit_Amount", "Credit_Amount", "Status"],
    )
    creditor_rows = load_rows(creditors_path, ["Account_Name", "Group_Name", "GSTIN"])

    print(f"  Day Book:       {len(daybook_rows):,} lines")
    print(f"  Search Voucher: {len(sv_rows):,} lines (before Approved filter)")
    print(f"  Creditors List: {len(creditor_rows):,} ledgers")

    # ---- Parse dates & build keys -------------------------------------------------
    for row in daybook_rows:
        row["_date"] = parse_tally_date(row["Date"])
        row["_bill_date"] = parse_tally_date(row.get("Bill_Date"))
        row["_key"] = voucher_key(row["_date"], row["Voucher_Type"], row["Voucher_No"])
        row["Debit"] = row["Debit"] or 0
        row["Credit"] = row["Credit"] or 0

    sv_approved = []
    for row in sv_rows:
        if str(row.get("Status", "")).strip().lower() != "approved":
            continue
        row["_date"] = parse_tally_date(row["Voucher_Date"])
        row["_key"] = voucher_key(row["_date"], row["Voucher_Type"], row["Voucher_No"])
        row["Debit_Amount"] = row["Debit_Amount"] or 0
        row["Credit_Amount"] = row["Credit_Amount"] or 0
        sv_approved.append(row)
    print(f"  Search Voucher: {len(sv_approved):,} lines after keeping Status = Approved only")

    # group Search Voucher (approved) lines by voucher key
    sv_by_voucher = {}
    for row in sv_approved:
        sv_by_voucher.setdefault(row["_key"], []).append(row)

    # (voucher_key, particulars) -> Fixed_Group_Name, from Day Book - used to
    # spot tax/duty/RCM lines inside the Search Voucher party candidates
    group_lookup = {}
    for row in daybook_rows:
        group_lookup[(row["_key"], row["Particulars"])] = row["Fixed_Group_Name"]

    # A specific Output ... on RCM ledger anywhere in the Day Book voucher is
    # the business-approved Eligible/Registered party override.
    output_rcm_flag_by_voucher = {}
    for row in daybook_rows:
        if is_output_rcm_ledger(row["Particulars"]):
            output_rcm_flag_by_voucher[row["_key"]] = True

    # voucher_key -> True/False, does ANY line in the whole voucher look like
    # an RCM entry (legacy Search Voucher flag plus the Day Book override)
    rcm_flag_by_voucher = {}
    gst_ledger_flag_by_voucher = {}  # non-RCM CGST/SGST/IGST present anywhere
    gst_types_by_voucher = {}
    for key, lines in sv_by_voucher.items():
        rcm = output_rcm_flag_by_voucher.get(key, False) or any(
            RCM_MARKER.upper() in (l["Particulars"] or "").upper() for l in lines
        )
        gst_ledger = any(
            any(tag in (l["Particulars"] or "").upper() for tag in ("CGST", "SGST", "IGST"))
            and RCM_MARKER.upper() not in (l["Particulars"] or "").upper()
            for l in lines
        )
        rcm_flag_by_voucher[key] = rcm
        gst_ledger_flag_by_voucher[key] = gst_ledger
        gst_types_by_voucher[key] = {
            tax_type
            for tax_type in ("CGST", "SGST", "IGST")
            if any(tax_type in (l["Particulars"] or "").upper() for l in lines)
        }

    # ---- Creditors lookup: normalised name -> GSTIN --------------------------------
    creditor_gstin = {}
    creditor_display_name = {}
    names_with_gstin = []  # only names that actually carry a GSTIN - the only
                            # ones worth suggesting as a "did you mean" match
    for row in creditor_rows:
        gstin = (row.get("GSTIN") or "").strip() if row.get("GSTIN") else ""
        for name_field in ("Account_Name", "Legal_Cheque_Name"):
            nm = row.get(name_field)
            if nm:
                key = norm_name(nm)
                if key not in creditor_gstin or (gstin and not creditor_gstin[key]):
                    creditor_gstin[key] = gstin
                    creditor_display_name[key] = str(nm).strip()
                if gstin:
                    names_with_gstin.append(str(nm).strip())

    def resolve_party(voucher_key_):
        lines = sv_by_voucher.get(voucher_key_, [])
        candidates = []
        creditor_candidates = []
        for l in lines:
            fg = group_lookup.get((voucher_key_, l["Particulars"]), "")
            if is_tax_duty_line(fg, l["Particulars"]):
                continue
            # Cost/expense lines are report rows, not the counterparty. Keeping
            # them out prevents self-resolved parties in month-end/JV vouchers.
            if fg and fg not in NEVER_COST_GROUPS:
                continue
            name_key = norm_name(l["Particulars"])
            amount = max(abs(l["Credit_Amount"]), abs(l["Debit_Amount"]))
            # A named party found in the Creditors List is more reliable than
            # a cash/bank settlement line. Consider both debit and credit sides
            # because Tally voucher types do not all place the party on one side.
            if creditor_gstin.get(name_key):
                creditor_candidates.append((amount, name_key, l))
            if l["Credit_Amount"] > 0 and l["Debit_Amount"] == 0:
                candidates.append(l)
        if creditor_candidates:
            creditor_candidates.sort(key=lambda item: item[0], reverse=True)
            _, name_key, _ = creditor_candidates[0]
            return clean_party_display(
                creditor_display_name.get(name_key, creditor_candidates[0][2]["Particulars"])
            )
        if not candidates:
            return ""
        candidates.sort(key=lambda l: l["Credit_Amount"], reverse=True)
        return clean_party_display(candidates[0]["Particulars"])

    # ---- Ask which Fixed_Group_Name(s) / Parent_Group_Name(s) to use ---------------
    fg_counts = {}
    for row in daybook_rows:
        fg_counts[row["Fixed_Group_Name"]] = fg_counts.get(row["Fixed_Group_Name"], 0) + 1
    fg_counts = dict(sorted(fg_counts.items(), key=lambda kv: -kv[1]))
    suggested_fg = [g for g in fg_counts if g not in NEVER_COST_GROUPS]

    selected_fg = ask_selection(
        fg_counts, suggested_fg,
        "Which Fixed_Group_Name(s) should be treated as cost/expense lines this run?"
    )

    pg_counts = {}
    for row in daybook_rows:
        if row["Fixed_Group_Name"] in selected_fg:
            pg_counts[row["Parent_Group_Name"]] = pg_counts.get(row["Parent_Group_Name"], 0) + 1
    pg_counts = dict(sorted(pg_counts.items(), key=lambda kv: -kv[1]))
    suggested_exclude_pg = [g for g in pg_counts if g in NEVER_COST_PARENT_GROUPS]

    excluded_pg = ask_selection(
        pg_counts, suggested_exclude_pg,
        "Within that selection, which Parent_Group_Name(s) should be EXCLUDED (not real procurement cost)?"
    )

    # ---- Build the final table ------------------------------------------------------
    print("\nBuilding the 80-20 table...")
    needs_review = []
    seen_review = set()  # (Unique_Voucher_ID, Issue) - a multi-line voucher would
                          # otherwise repeat the same flag once per line; only list it once

    def add_review(key_, party_, issue, suggestions=""):
        dedup_key = (key_, issue)
        if dedup_key in seen_review:
            return
        seen_review.add(dedup_key)
        needs_review.append({
            "Unique_Voucher_ID": key_,
            "Account Ledger (resolved)": party_,
            "Issue": issue,
            "Suggestions": suggestions,
        })

    out_rows = []
    for row in daybook_rows:
        if row["Fixed_Group_Name"] not in selected_fg:
            continue
        if row["Parent_Group_Name"] in excluded_pg:
            continue
        key = row["_key"]
        d = row["_date"]
        output_rcm = output_rcm_flag_by_voucher.get(key, False)
        rcm = rcm_flag_by_voucher.get(key, False) or output_rcm
        gst_ledger = gst_ledger_flag_by_voucher.get(key, False)

        party = resolve_party(key)
        gstin = creditor_gstin.get(norm_name(party), "")

        # Apply the approved party/GST master corrections.
        for override_name, (display_name, override_gstin) in PARTY_MASTER_OVERRIDES.items():
            if norm_name(party) == norm_name(override_name):
                party, gstin = display_name, override_gstin
                break

        # A bank settlement account is not itself a registered supplier. Only
        # GST-bearing bank-charge vouchers use the bank GST registration.
        if "kotak mahindra bank" in norm_name(party):
            if gst_ledger:
                party = "Kotak Mahindra Bank"
                gstin = "19AAACK4409J2ZG"
            else:
                gstin = ""
        if party and not gstin and not output_rcm:
            # only worth flagging if a SIMILARLY NAMED creditor actually has a GSTIN on
            # file - otherwise this is just an ordinary unregistered/cash/staff party,
            # which is not an error and would otherwise flood this list with noise
            suggestions = difflib.get_close_matches(party, names_with_gstin, n=3, cutoff=0.82)
            if suggestions:
                add_review(
                    key, party,
                    "No GSTIN found under this exact name, but a similarly named creditor DOES have one on file - check for a naming mismatch",
                    "; ".join(suggestions),
                )

        head = DEFAULT_HEAD
        # Formula Key must be driven strictly by Account_Name (the Day Book
        # Particulars field). Account_Head/Parent_Group_Name is intentionally
        # excluded so a parent such as "Salary & Staff Welfare" cannot make an
        # unrelated account such as "Other Staff Welfare Expenses" ineligible.
        kw = matched_keyword(row["Particulars"])

        # RCM does not override the Account_Name keyword rule. Therefore an RCM
        # row whose Account_Name contains CGST/SGST/IGST (or another configured
        # keyword) is Ineligible, while an RCM expense row without a keyword is
        # Eligible. The master Account Ledger rule below also takes precedence.
        ledger_is_always_ineligible = any(
            norm_name(party) == norm_name(ledger_name)
            for ledger_name in ALWAYS_INELIGIBLE_ACCOUNT_LEDGERS
        )
        if ledger_is_always_ineligible and not kw:
            kw = "BATCHING PLANT (DISEL)"
        eighty_twenty = "Ineligible" if (kw or ledger_is_always_ineligible) else "Eligible"

        # Sheet1 reference rule: GST Status is Registered whenever GST No. is
        # populated (including the GST-RCMechanism marker); otherwise it is
        # Unregistered. CGST/SGST/IGST Account_Name rows remain Ineligible via kw.
        rcm_tax_row = (
            kw in {"CGST", "SGST", "IGST"}
            and norm_name(gstin) == norm_name(GST_RCM_VALUE)
        )
        gst_status = "Registered" if gstin else "Unregistered"

        # Bank-specific GST rule. Formula Key always keeps priority for 80-20:
        # a bank row carrying an ineligible keyword remains Ineligible. Bank
        # rows with a blank Formula Key are Eligible. GST-ledger presence in
        # the Unique Voucher controls only the bank row's GST Status.
        is_bank_party = "bank" in norm_name(party)
        if is_bank_party:
            if not kw:
                eighty_twenty = "Eligible"
            gst_status = "Registered" if gst_ledger else "Unregistered"

        # Business-approved voucher override: an exact Output CGST/SGST/IGST
        # on RCM ledger anywhere in the Day Book voucher makes its concerned
        # party Eligible and Registered even when its GST No. is blank.
        if output_rcm:
            eighty_twenty = "Eligible"
            gst_status = "Registered"

        if not output_rcm and not is_bank_party and gstin and not gst_ledger and not rcm_tax_row:
            add_review(key, party, "GSTIN on file but voucher has no GST ledger (confirm exempt supply, or fix booking)")
        elif not output_rcm and not is_bank_party and gst_ledger and not gstin:
            add_review(key, party, "GST charged in voucher but no GSTIN on file for this party - update Creditors List")

        out_rows.append({
            "Unique_Voucher_ID": key,
            "Head": head,
            "Year": financial_year(d),
            "Month": date(d.year, d.month, 1),
            "Voucher_Date": d,
            "Voucher_Type": row["Voucher_Type"],
            "Voucher_No": row["Voucher_No"],
            "Bill_No": row.get("Bill_No") or "",
            "Bill_Date": row["_bill_date"],
            "Project Name": row.get("Cost_Centre") or "",
            # Sheet1 keeps this output column blank.
            "Narration": "",
            "Account_Head": row["Parent_Group_Name"],
            "Account_Name": row["Particulars"],
            "Account Ledger": party,
            "GST No.": gstin,
            "Debit": row["Debit"],
            "Credit": row["Credit"],
            "Closing": row["Debit"] - row["Credit"],
            "Formula Key": kw,
            "80-20": eighty_twenty,
            "GST Status": gst_status,
        })

    # Correct an occasional Tally export defect where two equal CGST rows are
    # exported even though Search Voucher confirms that the voucher contains
    # one CGST and one SGST ledger. This reproduces the approved Sheet1 result
    # without relying on a particular voucher number.
    rows_by_voucher = {}
    for output_row in out_rows:
        rows_by_voucher.setdefault(output_row["Unique_Voucher_ID"], []).append(output_row)
    for voucher_id, voucher_rows in rows_by_voucher.items():
        cgst_rows = [r for r in voucher_rows if r["Formula Key"] == "CGST"]
        sgst_rows = [r for r in voucher_rows if r["Formula Key"] == "SGST"]
        if (
            len(cgst_rows) == 2
            and not sgst_rows
            and cgst_rows[0]["Closing"] == cgst_rows[1]["Closing"]
        ):
            corrected = cgst_rows[1]
            corrected["Account_Name"] = re.sub(
                "CGST", "SGST", corrected["Account_Name"],
                count=1, flags=re.IGNORECASE,
            )
            corrected["Formula Key"] = "SGST"

    print(f"  {len(out_rows):,} reportable rows built.")
    print(f"  {len(needs_review):,} items flagged for review (see 'Needs Review' tab).")

    # ---- Write the workbook -----------------------------------------------------
    write_workbook(out_rows, needs_review, daybook_path)


def write_workbook(out_rows, needs_review, daybook_path):
    FONT = "Arial"
    NAVY = "1F4E78"
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "80-20 Table"
    headers = list(out_rows[0].keys()) if out_rows else [
        "Unique_Voucher_ID", "Head", "Year", "Month", "Voucher_Date", "Voucher_Type", "Voucher_No",
        "Bill_No", "Bill_Date", "Project Name", "Narration", "Account_Head", "Account_Name",
        "Account Ledger", "GST No.", "Debit", "Credit", "Closing", "Formula Key", "80-20", "GST Status",
    ]
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(1, c, h)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
    ws.freeze_panes = "A2"
    review_voucher_ids = {item["Unique_Voucher_ID"] for item in needs_review}
    REVIEW_FILL = PatternFill("solid", fgColor="FFF2CC")

    for r, row in enumerate(out_rows, start=2):
        for c, h in enumerate(headers, start=1):
            v = row[h]
            cell = ws.cell(r, c, v)
            if h == "Month":
                cell.number_format = "mmm-yy"
            elif h in ("Voucher_Date", "Bill_Date"):
                cell.number_format = "dd-mmm-yy"
            elif h in ("Debit", "Credit", "Closing"):
                cell.number_format = "#,##0.00"
            cell.font = Font(name=FONT, size=10)
            if row["Unique_Voucher_ID"] in review_voucher_ids:
                cell.fill = REVIEW_FILL

    # Excel filter dropdowns on every Main Table header.
    if ws.max_column:
        ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{max(ws.max_row, 1)}"

    widths = [22, 8, 11, 8, 10, 7, 7, 14, 10, 16, 30, 20, 30, 22, 16, 10, 10, 10, 10, 10, 26]
    for i, w in enumerate(widths[:len(headers)], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ---- Report sheet: live SUMIFS pivot -----------------------------------------
    ws2 = wb.create_sheet("Report")
    n = len(out_rows)
    last = n + 1  # data rows are 2..last on "80-20 Table"

    def col_letter(header_name):
        # look up the column letter by header text, not a hardcoded letter,
        # so this never silently breaks if the column order above changes
        return get_column_letter(headers.index(header_name) + 1)

    closing_col = col_letter("Closing")
    head_col = col_letter("Head")
    el_col = col_letter("80-20")
    gst_col = col_letter("GST Status")
    closing_rng = f"'80-20 Table'!${closing_col}$2:${closing_col}${last}"
    head_rng = f"'80-20 Table'!${head_col}$2:${head_col}${last}"
    el_rng = f"'80-20 Table'!${el_col}$2:${el_col}${last}"
    gst_rng = f"'80-20 Table'!${gst_col}$2:${gst_col}${last}"

    heads = sorted(set(row["Head"] for row in out_rows)) if out_rows else ["WIP"]
    r = 1
    hdr = ["Head", "80-20", "GST Status", "Sum of Closing"]
    for c, h in enumerate(hdr, start=1):
        cell = ws2.cell(r, c, h)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
    r += 1
    grand_total_cells = []
    for head in heads:
        head_total_cells = []
        for elig in ("Eligible", "Ineligible"):
            block_start = r
            for gst_status_label, formula_status in (
                ("Registered", "Registered"),
                ("Unregistered", "Unregistered"),
            ):
                ws2.cell(r, 1, head if (elig == "Eligible" and gst_status_label == "Registered") else None)
                ws2.cell(r, 2, elig if gst_status_label == "Registered" else None)
                ws2.cell(r, 3, gst_status_label)
                ws2.cell(r, 4, f'=SUMIFS({closing_rng},{head_rng},"{head}",{el_rng},"{elig}",{gst_rng},"{formula_status}")')
                r += 1
            ws2.cell(r, 2, f"{elig} Total")
            ws2.cell(r, 4, f"=SUM(D{block_start}:D{r-1})")
            head_total_cells.append(r)
            r += 1
        ws2.cell(r, 1, f"{head} Total")
        ws2.cell(r, 4, f"=D{head_total_cells[0]}+D{head_total_cells[1]}")
        grand_total_cells.append(r)
        r += 1
    ws2.cell(r, 1, "Grand Total")
    ws2.cell(r, 4, "=" + "+".join(f"D{x}" for x in grand_total_cells))
    for rr in range(1, r + 1):
        for cc in range(1, 5):
            cell = ws2.cell(rr, cc)
            if cell.value is not None:
                cell.font = Font(name=FONT, size=10, bold=(cc == 1))
                if cc == 4:
                    cell.number_format = "#,##0.00"
    for i, w in enumerate([18, 14, 26, 16], start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # Eligible-only registered/unregistered mix. Percentages are based on the
    # absolute Closing amount so credit signs cannot distort the GST mix.
    mix_col = 6
    mix_headers = ["Eligible GST Mix", "Eligible Amount", "% of Eligible"]
    for offset, h in enumerate(mix_headers):
        cell = ws2.cell(1, mix_col + offset, h)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
    ws2.cell(2, mix_col, "Registered")
    ws2.cell(3, mix_col, "Unregistered")
    ws2.cell(4, mix_col, "Eligible Total")
    ws2.cell(2, mix_col + 1, f'=SUMIFS({closing_rng},{el_rng},"Eligible",{gst_rng},"Registered")')
    ws2.cell(3, mix_col + 1, f'=SUMIFS({closing_rng},{el_rng},"Eligible",{gst_rng},"Unregistered")')
    ws2.cell(4, mix_col + 1, "=SUM(G2:G3)")
    for rr in range(2, 4):
        ws2.cell(rr, mix_col + 2, f'=IFERROR(ABS(G{rr})/SUM(ABS($G$2),ABS($G$3)),0)')
        ws2.cell(rr, mix_col + 2).number_format = "0.00%"
    for rr in range(2, 5):
        ws2.cell(rr, mix_col).font = Font(name=FONT, size=10, bold=(rr == 4))
        ws2.cell(rr, mix_col + 1).number_format = "#,##0.00"
    for cc, width in zip(range(mix_col, mix_col + 3), [24, 18, 16]):
        ws2.column_dimensions[get_column_letter(cc)].width = width

    # Pivot-style instant review sheet: compact counts and values by Head,
    # eligibility and GST status. It opens ready for filtering.
    wsp = wb.create_sheet("Instant Review Pivot")
    pivot_headers = ["Head", "80-20", "GST Status", "Line Count", "Sum of Closing", "% of Eligible Amount"]
    for c, h in enumerate(pivot_headers, start=1):
        cell = wsp.cell(1, c, h)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
    pivot_statuses = ["Registered", "Unregistered"]
    pr = 2
    for head in heads:
        for elig in ("Eligible", "Ineligible"):
            for status in pivot_statuses:
                matches = [x for x in out_rows if x["Head"] == head and x["80-20"] == elig and x["GST Status"] == status]
                if not matches:
                    continue
                amount = sum((x["Closing"] or 0) for x in matches)
                wsp.cell(pr, 1, head)
                wsp.cell(pr, 2, elig)
                wsp.cell(pr, 3, status)
                wsp.cell(pr, 4, len(matches))
                wsp.cell(pr, 5, amount)
                if elig == "Eligible":
                    eligible_head_total = sum(abs(x["Closing"] or 0) for x in out_rows if x["Head"] == head and x["80-20"] == "Eligible")
                    wsp.cell(pr, 6, abs(amount) / eligible_head_total if eligible_head_total else 0)
                pr += 1
    wsp.freeze_panes = "A2"
    wsp.auto_filter.ref = f"A1:F{max(pr - 1, 1)}"
    for rr in range(2, pr):
        wsp.cell(rr, 5).number_format = "#,##0.00"
        wsp.cell(rr, 6).number_format = "0.00%"
        for cc in range(1, 7):
            wsp.cell(rr, cc).font = Font(name=FONT, size=10)
    for i, w in enumerate([18, 14, 52, 12, 18, 20], start=1):
        wsp.column_dimensions[get_column_letter(i)].width = w

    # ---- Needs Review sheet -------------------------------------------------------
    ws3 = wb.create_sheet("Needs Review")
    rv_headers = ["Unique_Voucher_ID", "Account Ledger (resolved)", "Issue", "Suggestions"]
    for c, h in enumerate(rv_headers, start=1):
        cell = ws3.cell(1, c, h)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
    for r, item in enumerate(needs_review, start=2):
        for c, h in enumerate(rv_headers, start=1):
            cell = ws3.cell(r, c, item.get(h, ""))
            cell.font = Font(name=FONT, size=10)
            cell.fill = REVIEW_FILL
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for i, w in enumerate([22, 26, 46, 40], start=1):
        ws3.column_dimensions[get_column_letter(i)].width = w
    if not needs_review:
        ws3.cell(2, 1, "Nothing flagged - every resolved party matched a GSTIN cleanly, or was consistently unregistered.")
    ws3.freeze_panes = "A2"
    ws3.auto_filter.ref = f"A1:D{max(ws3.max_row, 1)}"

    out_dir = os.path.dirname(os.path.abspath(daybook_path))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"80-20_Table_{ts}.xlsx")
    wb.save(out_path)
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    main()
