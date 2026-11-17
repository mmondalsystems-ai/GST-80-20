"""Stable installation identity used only to distinguish backup filenames."""
import uuid

from .config import VAR_DIR

INSTALLATION_ID_FILE = VAR_DIR / "installation-id"


def installation_id() -> str:
    """Return a persistent random identifier; it is not a licence or hardware ID."""
    if INSTALLATION_ID_FILE.exists():
        return str(uuid.UUID(INSTALLATION_ID_FILE.read_text(encoding="ascii").strip()))
    value = str(uuid.uuid4())
    try:
        with INSTALLATION_ID_FILE.open("x", encoding="ascii") as handle:
            handle.write(value)
    except FileExistsError:
        value = INSTALLATION_ID_FILE.read_text(encoding="ascii").strip()
    return str(uuid.UUID(value))
