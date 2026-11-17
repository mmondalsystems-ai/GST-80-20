"""Render the disabled-by-default AI workspace on a fresh installation."""
import os
import tempfile
from pathlib import Path

scratch = Path(__file__).resolve().parent / ".tmp"
scratch.mkdir(exist_ok=True)
os.environ["VAR_DIR"] = tempfile.mkdtemp(prefix="gst8020-ai-route-", dir=scratch)
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@ai.test"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "TestPassword1234"
os.environ.pop("ANTHROPIC_API_KEY", None)

from starlette.requests import Request
from app.templating import templates
from app.models import User, AISetting
from app.ai_service import CAPABILITIES, settings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import Base


def main():
    request = Request({"type": "http", "method": "GET", "path": "/gst8020/ai",
                       "headers": [], "query_string": b"", "server": ("test", 80),
                       "scheme": "http", "session": {}})
    user = User(id=1, email="admin@ai.test", name="Administrator",
                password_hash="x", role="admin", is_active=True)
    finops = {"requests": 0, "tokens": 0, "cost": 0, "success_rate": 0,
              "latency": 0, "by_capability": {}, "rows": []}
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as db:
        fresh_settings = settings(db)
    assert fresh_settings.model == "claude-sonnet-5" and fresh_settings.enabled is False
    rendered = templates.get_template("gst/ai.html").render(
        request=request, user=user, nav="ai", settings=fresh_settings,
        key_status={"configured": False, "source": "Not configured", "fingerprint": "", "updated_at": None},
        capabilities=CAPABILITIES, runs=[], result_rows=[],
        creditors=[], interactions=[], selected=None, suggestions=[], finops=finops,
        flash=None)
    assert "AI Workspace" in rendered and "Disabled by default" in rendered
    assert "Anthropic key not configured" in rendered
    assert "Anthropic usage" in rendered
    print("PASS: fresh AI Workspace is disabled and renders without an API key")


if __name__ == "__main__":
    main()
