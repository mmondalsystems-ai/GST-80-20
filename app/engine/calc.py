"""Read-only portal summaries of rows produced by the approved Python logic.

There is one calculation engine: ``golden.calculate``. Supplementary project
gaps and vendor rankings never reclassify a row or feed the canonical workbook.
"""
from collections import defaultdict
import re

from .golden import calculate, INELIGIBLE_KEYWORDS
from .readers import key


DEFAULTS = {
    "include_fixed_groups": [],
    "exclude_parent_groups": ["TDS Receivable"],
    "ineligible_keywords": INELIGIBLE_KEYWORDS.copy(),
    # These three values apply only to supplementary portal indicators.
    "threshold_pct": 80.0,
    "shortfall_tax_rate": 18.0,
    "alert_buffer_pct": 82.0,
}


def merged_config(overrides: dict | None) -> dict:
    """Retain recognised settings while locking Python's keyword master."""
    cfg = {name: value.copy() if isinstance(value, list) else value
           for name, value in DEFAULTS.items()}
    for name, value in (overrides or {}).items():
        if name in cfg and name != "ineligible_keywords":
            cfg[name] = value
    return cfg


def cluster_key(name: str) -> str:
    """Group visually similar names for a review list, never for GST matching."""
    value = key(name)
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"[-–]\s*(RET|CREDITOR|CR|A/?C).*$", " ", value)
    value = re.sub(r"\b(PVT|PRIVATE|LTD|LIMITED|LLP|CO|COMPANY|AND|&|THE)\b", " ", value)
    value = re.sub(r"[^A-Z0-9 ]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return " ".join(w[:-1] if len(w) > 4 and w.endswith("S") else w
                    for w in value.split())


def summarise(rows, cfg=None):
    """Use the approved Report sheet's eligible GST mix calculation."""
    cfg = merged_config(cfg)
    agg = defaultdict(float)
    for row in rows:
        agg[(row["head"], row["eligibility"], row["gst_status"])] += row["closing"]
    registered = sum(amount for (_, eligibility, status), amount in agg.items()
                     if eligibility == "Eligible" and status == "Registered")
    unregistered = sum(amount for (_, eligibility, status), amount in agg.items()
                       if eligibility == "Eligible" and status == "Unregistered")
    eligible = registered + unregistered
    mix_denominator = abs(registered) + abs(unregistered)
    # Approved Report!H2 = ABS(G2)/SUM(ABS(G2),ABS(G3)).
    pct = abs(registered) / mix_denominator * 100 if mix_denominator else 0.0
    # The threshold/gap is an optional portal indicator, not a script output.
    required = eligible * cfg["threshold_pct"] / 100
    gap = max(0.0, required - registered)
    return {
        "eligible": eligible, "registered": registered,
        "unregistered": unregistered,
        "ineligible": sum(amount for (_, eligibility, _), amount in agg.items()
                          if eligibility == "Ineligible"),
        "pct": pct, "required": required, "gap": gap,
        "exposure": gap * cfg["shortfall_tax_rate"] / 100,
        "rows": len(rows),
        "agg": {f"{head}|{eligibility}|{status}": amount
                for (head, eligibility, status), amount in agg.items()},
    }


def summarise_by_project(rows, cfg=None):
    by_project = defaultdict(list)
    for row in rows:
        by_project[row["project"] or "(no cost centre)"].append(row)
    return {name: summarise(items, cfg) for name, items in sorted(by_project.items())}


def summarise_portfolio(rows, cfg=None):
    total = summarise(rows, cfg)
    projects = summarise_by_project(rows, cfg)
    total["gap"] = sum(project["gap"] for project in projects.values())
    total["exposure"] = sum(project["exposure"] for project in projects.values())
    total["below_projects"] = sum(project["gap"] > 0 for project in projects.values())
    total["project_count"] = len(projects)
    return total


def vendor_concentration(rows, status="Unregistered", eligibility="Eligible", limit=None):
    """Supplementary ranking from golden rows, with no classification effect."""
    by = defaultdict(lambda: {"amount": 0.0, "names": set(), "vouchers": set(),
                              "gstin": "", "settlement": False})
    for row in rows:
        if row["eligibility"] != eligibility or (status and row["gst_status"] != status):
            continue
        bucket = by[cluster_key(row["ledger"]) or key(row["ledger"])]
        bucket["amount"] += row["closing"]
        bucket["names"].add(row["ledger"])
        bucket["vouchers"].add(row["voucher_id"])
        bucket["gstin"] = bucket["gstin"] or row["gstin"]
        if not str(row.get("party_kind", "Vendor")).startswith("Vendor"):
            bucket["settlement"] = True
    out = [{"cluster": name, "amount": values["amount"],
            "names": sorted(values["names"]), "variants": len(values["names"]),
            "vouchers": len(values["vouchers"]), "gstin": values["gstin"],
            "settlement": values["settlement"]}
           for name, values in by.items()]
    out.sort(key=lambda item: -item["amount"])
    return out[:limit] if limit else out


def report_table(rows, cfg=None):
    """The approved Report sheet's sum-of-Closing blocks for portal display."""
    agg = defaultdict(float)
    for row in rows:
        agg[(row["head"], row["eligibility"], row["gst_status"])] += row["closing"]
    out = []
    for head in sorted({item[0] for item in agg}):
        for eligibility in ("Eligible", "Ineligible"):
            # The approved script emits both GST-status rows even when one is
            # zero, followed by the eligibility subtotal.
            matches = [(status, agg.get((head, eligibility, status), 0.0))
                       for status in ("Registered", "Unregistered")]
            for status, amount in matches:
                out.append({"head": head, "elig": eligibility,
                            "status": status, "amount": amount, "total": False})
            out.append({"head": head, "elig": eligibility,
                        "status": f"{eligibility} total",
                        "amount": sum(amount for _, amount in matches), "total": True})
    return out
