"""Best-effort offline, signed 14-day licence enforcement.

The issuer's private key is never deployed. Local source and clock control mean
this cannot be tamper-proof; it is a transparent commercial licence control.
"""
import base64
import datetime as dt
import json
from pathlib import Path
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from sqlalchemy.exc import IntegrityError

from .config import BASE_DIR, VAR_DIR
from .models import LicenseActivation

PUBLIC_KEY_PATH = BASE_DIR / "app" / "license_public_key.pem"
LICENSE_FILE = VAR_DIR / "license.json"
INSTALLATION_ID_FILE = VAR_DIR / "installation-id"
TERM_DAYS = 14
CLOCK_TOLERANCE = dt.timedelta(minutes=5)


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def as_utc(value):
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def installation_id():
    """Persistent random installation identifier, not a hardware fingerprint."""
    if INSTALLATION_ID_FILE.exists():
        value = INSTALLATION_ID_FILE.read_text(encoding="ascii").strip()
        return str(uuid.UUID(value))
    value = str(uuid.uuid4())
    try:
        with INSTALLATION_ID_FILE.open("x", encoding="ascii") as handle:
            handle.write(value)
    except FileExistsError:
        value = INSTALLATION_ID_FILE.read_text(encoding="ascii").strip()
    return str(uuid.UUID(value))


def verify_license_file(path, expected_installation_id):
    """Verify a prospective licence without starting its activation period."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    payload = document["payload"]
    signature = base64.b64decode(document["signature"], validate=True)
    public_key = serialization.load_pem_public_key(PUBLIC_KEY_PATH.read_bytes())
    public_key.verify(signature, canonical(payload))
    if payload.get("schema") != 1 or payload.get("duration_days") != TERM_DAYS:
        raise ValueError("Licence terms are not supported")
    if payload.get("installation_id") != expected_installation_id:
        raise ValueError("Licence belongs to another installation")
    license_id = str(uuid.UUID(payload["license_id"]))
    customer = str(payload["customer"]).strip()
    if not customer or len(customer) > 160:
        raise ValueError("Missing customer")
    return payload, license_id, customer


@dataclass(frozen=True)
class LicenseStatus:
    code: str
    message: str
    installation_id: str
    license_id: str = ""
    customer: str = ""
    activated_at: dt.datetime | None = None
    expires_at: dt.datetime | None = None

    @property
    def read_only(self):
        return self.code != "active"


def evaluate(db, now=None):
    """Verify signature and duration; record first activation in the DB."""
    now = as_utc(now or utcnow())
    try:
        install_id = installation_id()
    except (OSError, ValueError):
        return LicenseStatus("invalid", "Installation ID is unavailable. Contact the licence issuer.", "")
    if not LICENSE_FILE.is_file():
        return LicenseStatus("missing", "No licence installed. The portal is read-only.", install_id)
    try:
        payload, license_id, customer = verify_license_file(LICENSE_FILE, install_id)
    except (OSError, ValueError, KeyError, TypeError, InvalidSignature):
        return LicenseStatus("invalid", "Licence is invalid or belongs to another installation.", install_id)

    activation = db.get(LicenseActivation, license_id)
    if activation is None:
        activation = LicenseActivation(license_id=license_id, installation_id=install_id,
                                       activated_at=now, last_seen_at=now)
        db.add(activation)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            activation = db.get(LicenseActivation, license_id)
            if activation is None:
                raise
    if activation.installation_id != install_id:
        return LicenseStatus("invalid", "Licence activation belongs to another installation.", install_id)
    activated_at = as_utc(activation.activated_at)
    last_seen_at = as_utc(activation.last_seen_at)
    expires_at = activated_at + dt.timedelta(days=TERM_DAYS)
    if now + CLOCK_TOLERANCE < last_seen_at or now + CLOCK_TOLERANCE < activated_at:
        return LicenseStatus("clock", "System clock moved backwards. The portal is read-only.",
                             install_id, license_id, customer, activated_at, expires_at)
    if now >= expires_at:
        return LicenseStatus("expired", "The 14-day licence has expired. The portal is read-only.",
                             install_id, license_id, customer, activated_at, expires_at)
    if now.date() > last_seen_at.date():
        activation.last_seen_at = now
        db.commit()
    return LicenseStatus("active", "Licence active.", install_id, license_id,
                         customer, activated_at, expires_at)
