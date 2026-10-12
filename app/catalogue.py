"""The catalogue of solutions shown on the portal home page.

Adding a module to the portal means adding an entry here and mounting its
router in main.py -- the home page, the switcher and the access checks all read
from this one list.
"""
SOLUTIONS = [
    {
        "slug": "gst8020",
        "name": "GST 80:20 Input Credit",
        "tagline": "Monthly and year-to-date registered-supplier share, per project",
        "description": "Ingests the Day Book, Search Voucher and Creditors exports, resolves the "
                       "party behind every cost line, applies the reverse-charge and keyword rules, "
                       "and tracks the 80% registered-procurement threshold with the shortfall "
                       "exposure it implies.",
        "status": "live",
        "url": "/gst8020",
        "owner": "Accounts — Indirect Tax",
        "icon": "8020",
    },
    {
        "slug": "msme-ageing",
        "name": "MSME Payment Ageing",
        "tagline": "Section 43B(h) exposure on overdue MSME supplier payments",
        "description": "The creditors master already carries MSME_Category, MSME_No and "
                       "MSME_Activity. This module would age open MSME payables against the 45-day "
                       "limit and flag the disallowance risk before year end.",
        "status": "planned", "url": "", "owner": "Accounts — Direct Tax", "icon": "MSME",
    },
    {
        "slug": "tds-reconciliation",
        "name": "TDS Deduction Review",
        "tagline": "Section-wise TDS deducted against payments booked",
        "description": "The Day Book carries a TDS group excluded from the 80:20 report. This "
                       "module would check deduction rates by section and surface short or missed "
                       "deductions before the quarterly return.",
        "status": "planned", "url": "", "owner": "Accounts — Direct Tax", "icon": "TDS",
    },
    {
        "slug": "vendor-master",
        "name": "Vendor Master Health",
        "tagline": "Duplicates, missing GSTIN and PAN across the creditors master",
        "description": "A standing view of master-data quality — near-duplicate ledgers, missing "
                       "or mistyped GSTINs, PAN mismatches — feeding every other module that "
                       "depends on the creditors list.",
        "status": "planned", "url": "", "owner": "Accounts — Payables", "icon": "VMH",
    },
    {
        "slug": "project-cost",
        "name": "Project Cost Dashboard",
        "tagline": "Cost-centre spend by head, month over month",
        "description": "The same Day Book ingestion, read for management reporting rather than "
                       "compliance: spend by project, head and month, with budget comparison.",
        "status": "planned", "url": "", "owner": "Finance — MIS", "icon": "PCD",
    },
]

BY_SLUG = {s["slug"]: s for s in SOLUTIONS}
