"""Fresh default password and legacy/configured bootstrap preservation."""
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from app import config


def main():
    with tempfile.TemporaryDirectory(prefix="gst8020-bootstrap-") as folder:
        with patch.object(config, "VAR_DIR", Path(folder)), patch.dict(os.environ):
            os.environ.pop("BOOTSTRAP_ADMIN_PASSWORD", None)
            assert config._bootstrap_password() == "admin123456789"
            saved = Path(folder) / "first-admin-password.txt"
            assert saved.read_text() == "admin123456789"
            saved.write_text("ExistingBootstrapPassword")
            assert config._bootstrap_password() == "ExistingBootstrapPassword"
            os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "ExplicitOverridePassword"
            assert config._bootstrap_password() == "ExplicitOverridePassword"
    print("PASS: agreed fresh password; saved and configured passwords preserved")


if __name__ == "__main__":
    main()
