"""SQLite dependencies exclude PostgreSQL; detection does not import its driver."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    common = (root / "requirements.txt").read_text()
    assert "psycopg" not in common.lower()
    optional = (root / "requirements-postgres.txt").read_text()
    assert "-r requirements.txt" in optional and "psycopg2-binary==" in optional
    for url, expected in (("sqlite:///:memory:", "sqlite"),
                          ("postgresql+psycopg2://user:password@localhost/example", "postgresql")):
        env = os.environ.copy()
        env["DATABASE_URL"] = url
        output = subprocess.check_output([sys.executable, "-m", "app.deploy_check", "database-kind"],
                                         cwd=root, env=env, text=True)
        assert output.strip() == expected
    print("PASS: SQLite base requirements omit PostgreSQL; configured DB detection works")


if __name__ == "__main__":
    main()
