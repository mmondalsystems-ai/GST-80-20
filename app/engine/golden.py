"""Web-safe port of the client-approved ``reference/80-20.py`` algorithm.

The legacy program is the calculation authority.  This module changes only the
input/output boundary: rows arrive from the web upload and dictionaries are
returned for persistence.  Party selection, filtering, keyword handling, bank
treatment, built-in party-master corrections and the duplicate-CGST repair mirror the
approved script.
"""
from collections import defaultdict
import difflib
import re

from .readers import parse_date
from .gstin import validate as validate_gstin

INELIGIBLE_KEYWORDS = [
    "CGST", "SGST", "IGST", "ESIC", "EPFO", "INTEREST", "SALARY",
    "DEPRECIATION", "DEPRECEATION", "P TAX", "PROPERTY TAX",
    "BATCHING PLANT (DISEL)", "BATCHING PLANT (DIESEL)",
]
DEFAULT_HEAD = "Expenses"
ALWAYS_INELIGIBLE_ACCOUNT_LEDGERS = {
    "AMA FUELS & TECHNOLOGIES PVT LTD", "Pugalia Automobiles",
}
PARTY_MASTER_OVERRIDES = {
    "Oswal Towers LLP": ("Oswal Towers LLP", "19AADFO9095N1ZC"),
    "Oswal Imprints": ("Oswal Imprints", "19AAFFO5761M2ZM"),
    "AMA FUELS & TECHNOLOGIES PVT LTD":
        ("AMA FUELS & TECHNOLOGIES PVT LTD", "19AATCA6093M1ZQ"),
    "Bajaj Housing Finance Limited (Loan)":
        ("Bajaj Housing Finance Limited (Loan)", ""),
}
NEVER_COST_GROUPS = {
    "Sundry Creditors", "Sundry Debtors", "Cash Accounts", "Bank Accounts",
    "CGST", "SGST", "IGST", "TDS", "TCS", "Income (Revenue)", "Sales Accounts",
    "Advance from Flatholders", "Secured Loans", "Unsecured Loans",
    "Current Liabilities", "Duties & Taxes", "Deposits (Assets)", "Investments",
    "Expenditure Accounts", "Loans & Advances (Assets)",
}
NEVER_COST_PARENT_GROUPS = {"TDS Receivable"}
TAX_DUTY_FIXED_GROUPS = {"CGST", "SGST", "IGST", "TDS", "TCS"}
RCM_MARKER = "RCM"
GST_RCM_VALUE = "GST-RCMechanism"


def norm_name(value):
    value = re.sub(r"\s+", " ", str(value or "").strip()).lower()
    value = re.sub(r"^m\s*/?\s*s[,.]?\s+", "", value)
    value = re.sub(r"\s*[-(]?\s*creditors?\s*\)?\s*$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_party_display(value):
    value = re.sub(r"\s+", " ", str(value or "").strip())
    value = re.sub(r"^m\s*/?\s*s[,.]?\s+", "", value, flags=re.I)
    value = re.sub(r"\s*[-(]?\s*creditors?\s*\)?\s*$", "", value, flags=re.I)
    return value.strip()


def voucher_key(d, vtype, vno):
    vno = str(int(vno)) if isinstance(vno, (int, float)) and float(vno).is_integer() else str(vno).strip()
    return f"{d.isoformat()}|{str(vtype).strip().upper()}|{vno}"


def financial_year(d):
    return f"{d.year}-{d.year + 1}" if d.month >= 4 else f"{d.year - 1}-{d.year}"


def is_tax_duty_line(group, particulars):
    return group in TAX_DUTY_FIXED_GROUPS or RCM_MARKER in str(particulars or "").upper()


def matched_keyword(text, keywords):
    upper = str(text or "").upper()
    return next((keyword for keyword in keywords if keyword.upper() in upper), "")


def _number(value):
    if value in (None, ""):
        return 0
    return value


def calculate(daybook, voucher, creditors, cfg=None):
    cfg = cfg or {}
    keywords = INELIGIBLE_KEYWORDS
    never_groups = NEVER_COST_GROUPS
    excluded_parents = set(cfg.get("exclude_parent_groups", NEVER_COST_PARENT_GROUPS))
    explicitly_included = cfg.get("include_fixed_groups")

    for row in daybook:
        row["_date"] = parse_date(row.get("Date"))
        row["_bill_date"] = parse_date(row.get("Bill_Date"))
        if not row["_date"]:
            raise ValueError(f"Could not parse date value: {row.get('Date')!r}")
        row["_key"] = voucher_key(row["_date"], row.get("Voucher_Type"), row.get("Voucher_No"))
        row["Debit"], row["Credit"] = _number(row.get("Debit")), _number(row.get("Credit"))

    approved = []
    for row in voucher:
        if str(row.get("Status", "")).strip().lower() != "approved":
            continue
        row["_date"] = parse_date(row.get("Voucher_Date"))
        if not row["_date"]:
            raise ValueError(f"Could not parse date value: {row.get('Voucher_Date')!r}")
        row["_key"] = voucher_key(row["_date"], row.get("Voucher_Type"), row.get("Voucher_No"))
        row["Debit_Amount"] = _number(row.get("Debit_Amount"))
        row["Credit_Amount"] = _number(row.get("Credit_Amount"))
        approved.append(row)

    by_voucher = defaultdict(list)
    for row in approved:
        by_voucher[row["_key"]].append(row)
    group_lookup = {(r["_key"], r.get("Particulars")): r.get("Fixed_Group_Name") for r in daybook}
    rcm_flags, gst_flags = {}, {}
    for uid, lines in by_voucher.items():
        rcm_flags[uid] = any(RCM_MARKER in str(x.get("Particulars") or "").upper() for x in lines)
        gst_flags[uid] = any(
            any(tag in str(x.get("Particulars") or "").upper() for tag in ("CGST", "SGST", "IGST"))
            and RCM_MARKER not in str(x.get("Particulars") or "").upper() for x in lines)

    creditor_gstin, creditor_display, names_with_gstin = {}, {}, []
    for row in creditors:
        gstin = str(row.get("GSTIN", row.get("gstin", "")) or "").strip()
        for field in ("Account_Name", "Legal_Cheque_Name", "account_name", "legal_name"):
            name = row.get(field)
            if not name:
                continue
            nk = norm_name(name)
            if nk not in creditor_gstin or (gstin and not creditor_gstin[nk]):
                creditor_gstin[nk], creditor_display[nk] = gstin, str(name).strip()
            if gstin:
                names_with_gstin.append(str(name).strip())

    def resolve_party(uid):
        candidates, known_candidates = [], []
        for line in by_voucher.get(uid, []):
            fg = group_lookup.get((uid, line.get("Particulars")), "")
            if is_tax_duty_line(fg, line.get("Particulars")):
                continue
            if fg and fg not in never_groups:
                continue
            nk = norm_name(line.get("Particulars"))
            amount = max(abs(line["Credit_Amount"]), abs(line["Debit_Amount"]))
            if creditor_gstin.get(nk):
                known_candidates.append((amount, nk, line))
            if line["Credit_Amount"] > 0 and line["Debit_Amount"] == 0:
                candidates.append(line)
        if known_candidates:
            known_candidates.sort(key=lambda item: item[0], reverse=True)
            _, nk, line = known_candidates[0]
            return clean_party_display(creditor_display.get(nk, line.get("Particulars")))
        if not candidates:
            return ""
        candidates.sort(key=lambda line: line["Credit_Amount"], reverse=True)
        return clean_party_display(candidates[0].get("Particulars"))

    all_groups = {r.get("Fixed_Group_Name") for r in daybook}
    selected_groups = set(explicitly_included) if explicitly_included else all_groups - never_groups
    review, seen_review, output = [], set(), []

    def add_review(uid, party, issue, suggestions=""):
        if (uid, issue) in seen_review:
            return
        seen_review.add((uid, issue))
        review.append({"voucher_id": uid, "ledger": party, "reason": issue,
                       "suggestions": suggestions})

    for row in daybook:
        if row.get("Fixed_Group_Name") not in selected_groups or row.get("Parent_Group_Name") in excluded_parents:
            continue
        uid, d = row["_key"], row["_date"]
        party = resolve_party(uid)
        gstin = creditor_gstin.get(norm_name(party), "")
        for original, replacement in PARTY_MASTER_OVERRIDES.items():
            if norm_name(party) == norm_name(original):
                party, gstin = replacement
                break
        gst_ledger = gst_flags.get(uid, False)
        if "kotak mahindra bank" in norm_name(party):
            if gst_ledger:
                party, gstin = "Kotak Mahindra Bank", "19AAACK4409J2ZG"
            else:
                gstin = ""
        raw_party = party
        party_kind = "Vendor"
        suggestions = [] if gstin or not party else difflib.get_close_matches(party, names_with_gstin, n=3, cutoff=.82)
        if suggestions:
            add_review(uid, party, "No GSTIN found under this exact name, but a similarly named creditor DOES have one on file - check for a naming mismatch", "; ".join(suggestions))

        keyword = matched_keyword(row.get("Particulars"), keywords)
        forced = any(norm_name(party) == norm_name(x) for x in ALWAYS_INELIGIBLE_ACCOUNT_LEDGERS)
        if forced and not keyword:
            keyword = "BATCHING PLANT (DISEL)"
        eligibility = "Ineligible" if keyword or forced else "Eligible"
        rcm_tax_row = keyword in {"CGST", "SGST", "IGST"} and norm_name(gstin) == norm_name(GST_RCM_VALUE)
        status = "Registered" if gstin else "Unregistered"
        is_bank = "bank" in norm_name(party)
        if is_bank:
            if not keyword:
                eligibility = "Eligible"
            status = "Registered" if gst_ledger else "Unregistered"
        if not is_bank and gstin and not gst_ledger and not rcm_tax_row:
            add_review(uid, party, "GSTIN on file but voucher has no GST ledger (confirm exempt supply, or fix booking)")
        elif not is_bank and gst_ledger and not gstin:
            add_review(uid, party, "GST charged in voucher but no GSTIN on file for this party - update Creditors List")

        # Structural validation is an observation only. The source script
        # classifies any populated GST No. as Registered, even if invalid.
        flags = []
        if gstin and gstin != GST_RCM_VALUE:
            code, _ = validate_gstin(gstin)
            if code != "ok":
                flags.append(f"gstin_{code}")

        output.append({
            "voucher_id": uid, "head": DEFAULT_HEAD, "year": financial_year(d),
            "month": d.strftime("%b-%y"), "voucher_date": d,
            "voucher_type": row.get("Voucher_Type"), "voucher_no": row.get("Voucher_No"),
            "bill_no": row.get("Bill_No") or "", "bill_date": row["_bill_date"],
            "project": row.get("Cost_Centre") or "", "narration": "",
            "source_narration": row.get("Narration") or "",
            "account_head": row.get("Parent_Group_Name") or "",
            "account_name": row.get("Particulars") or "", "ledger": party,
            "ledger_raw": raw_party, "gstin": gstin, "debit": row["Debit"],
            "credit": row["Credit"], "closing": row["Debit"] - row["Credit"],
            "formula_key": keyword, "eligibility": eligibility, "gst_status": status,
            "rcm": rcm_flags.get(uid, False), "party_kind": party_kind,
            "party_source": "Search Voucher",
            "flags": ",".join(flags),
        })

    grouped = defaultdict(list)
    for row in output:
        grouped[row["voucher_id"]].append(row)
    for rows in grouped.values():
        cgst = [r for r in rows if r["formula_key"] == "CGST"]
        sgst = [r for r in rows if r["formula_key"] == "SGST"]
        if len(cgst) == 2 and not sgst and cgst[0]["closing"] == cgst[1]["closing"]:
            cgst[1]["account_name"] = re.sub("CGST", "SGST", cgst[1]["account_name"], count=1, flags=re.I)
            cgst[1]["formula_key"] = "SGST"

    reviewed = {item["voucher_id"] for item in review}
    for row in output:
        if row["voucher_id"] in reviewed:
            row["flags"] = ",".join(filter(None, (row["flags"], "needs_review")))
    rectifications = []
    for item in review:
        related = grouped.get(item["voucher_id"], [])
        first = related[0] if related else {}
        rectifications.append({
            "voucher_id": item["voucher_id"], "ledger": item["ledger"],
            "account_name": first.get("account_name", ""),
            "project": first.get("project", ""),
            "amount": sum(row["closing"] for row in related),
            "reason": item["reason"], "suggestions": item["suggestions"],
        })
    return {"rows": output, "rectifications": rectifications, "exceptions": review,
            "unmatched": [], "config": cfg}
