"""Security regression checks that do not call external services."""
import asyncio
import html
import io
import os
from pathlib import Path
import re
import tempfile

TEST_VAR = tempfile.mkdtemp(prefix="gst8020-hardening-test-")
os.environ["VAR_DIR"] = TEST_VAR
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@hardening.test"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "HardeningPassword1"

from fastapi import UploadFile
from fastapi.testclient import TestClient

from app.main import app
from app.engine import readers as R
from app.routers import gst8020


def csrf(page: str) -> str:
    return html.unescape(re.search(
        r'name="csrf_token_value" value="([^"]+)"', page).group(1))


def main():
    with TestClient(app) as client:
        login_page = client.get("/login")
        assert login_page.headers["x-content-type-options"] == "nosniff"
        assert login_page.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in login_page.headers["content-security-policy"]
        assert login_page.headers["cache-control"] == "no-store"
        token = csrf(login_page.text)
        for _ in range(8):
            response = client.post("/login", data={
                "email": "admin@hardening.test", "password": "wrong-password",
                "csrf_token_value": token,
            })
            assert response.status_code == 401
        blocked = client.post("/login", data={
            "email": "admin@hardening.test", "password": "HardeningPassword1",
            "csrf_token_value": token,
        })
        assert blocked.status_code == 429 and blocked.headers["retry-after"] == "600"

    original_bytes, original_mb = gst8020.MAX_UPLOAD_BYTES, gst8020.MAX_UPLOAD_MB
    gst8020.MAX_UPLOAD_BYTES, gst8020.MAX_UPLOAD_MB = 4, 1
    try:
        too_large = UploadFile(file=io.BytesIO(b"12345"), filename="large.xlsx")
        try:
            asyncio.run(gst8020.read_xlsx_upload(too_large, "Test workbook"))
            raise AssertionError("oversized upload should be rejected")
        except R.IngestError as exc:
            assert "upload limit" in str(exc)
        wrong_extension = UploadFile(file=io.BytesIO(b"PK"), filename="legacy.xls")
        try:
            asyncio.run(gst8020.read_xlsx_upload(wrong_extension, "Test workbook"))
            raise AssertionError("non-xlsx upload should be rejected")
        except R.IngestError as exc:
            assert ".xlsx" in str(exc)
    finally:
        gst8020.MAX_UPLOAD_BYTES, gst8020.MAX_UPLOAD_MB = original_bytes, original_mb
    print("PASS: security headers, sign-in throttling and upload boundary")


if __name__ == "__main__":
    main()
