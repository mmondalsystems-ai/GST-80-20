"""Controlled overlays for business rules approved after the original workbook."""
from app.engine.golden import is_output_rcm_ledger, voucher_key
from app.engine.readers import parse_date, read_daybook


def output_rcm_voucher_ids(reference_dir):
    return {
        voucher_key(parse_date(row["Date"]), row["Voucher_Type"], row["Voucher_No"])
        for row in read_daybook(reference_dir / "DayBookRegister.xlsx")
        if is_output_rcm_ledger(row.get("Particulars"))
    }


def overlay_detail_rows(rows, affected_ids):
    """Apply the Oct-2026 RCM rule to rows from the earlier golden workbook."""
    if not rows:
        return rows
    headers = list(rows[0])
    uid_col = headers.index("Unique_Voucher_ID")
    eligibility_col = headers.index("80-20")
    status_col = headers.index("GST Status")
    updated = [tuple(headers)]
    for source in rows[1:]:
        row = list(source)
        if row[uid_col] in affected_ids:
            row[eligibility_col] = "Eligible"
            row[status_col] = "Registered"
        updated.append(tuple(row))
    return updated


def expected_review_rows(rows, affected_ids):
    if not rows:
        return rows
    return [rows[0], *(row for row in rows[1:] if row[0] not in affected_ids)]


def expected_pivot_rows(detail_rows):
    headers = list(detail_rows[0])
    records = [dict(zip(headers, row)) for row in detail_rows[1:]]
    result = [("Head", "80-20", "GST Status", "Line Count", "Sum of Closing",
               "% of Eligible Amount")]
    for head in sorted({row["Head"] for row in records}):
        denominator = sum(
            abs(row["Closing"] or 0)
            for row in records
            if row["Head"] == head and row["80-20"] == "Eligible"
        )
        for eligibility in ("Eligible", "Ineligible"):
            for status in ("Registered", "Unregistered"):
                matches = [
                    row for row in records
                    if row["Head"] == head and row["80-20"] == eligibility
                    and row["GST Status"] == status
                ]
                if not matches:
                    continue
                amount = sum(row["Closing"] or 0 for row in matches)
                percentage = (
                    abs(amount) / denominator if denominator else 0
                ) if eligibility == "Eligible" else None
                result.append((head, eligibility, status, len(matches), amount, percentage))
    return result
