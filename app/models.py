"""Database tables.

The shape follows the workflow: a Run is one month's calculation, frozen as
RunRow records so a frozen month can always be reopened exactly as it was.
Review outcomes and group-selection settings are stored separately and audited.
The ledger-override table is retained solely to read historical records; new
calculations never use it.
"""
import datetime as dt
from sqlalchemy import (String, Integer, Float, Boolean, DateTime, Text, ForeignKey,
                        UniqueConstraint, Index)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def now():
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")   # admin | preparer | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def can_edit(self):    return self.role in ("admin", "preparer")

    @property
    def is_admin(self):    return self.role == "admin"


class LicenseActivation(Base):
    """First use of each signed offline licence on this installation."""
    __tablename__ = "license_activations"
    license_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(36), index=True)
    activated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class Run(Base):
    """One month's calculation, kept as a permanent snapshot."""
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(120))
    period_month: Mapped[str] = mapped_column(String(10), index=True)     # "Jul-26"
    period_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    financial_year: Mapped[str] = mapped_column(String(12), index=True)   # "2026-2027"
    status: Mapped[str] = mapped_column(String(20), default="draft")      # draft | frozen | superseded
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    frozen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_files: Mapped[str] = mapped_column(Text, default="")           # JSON
    config_snapshot: Mapped[str] = mapped_column(Text, default="{}")      # JSON: masters as used
    stats: Mapped[str] = mapped_column(Text, default="{}")                # JSON: headline figures
    notes: Mapped[str] = mapped_column(Text, default="")

    created_by: Mapped["User"] = relationship()
    rows: Mapped[list["RunRow"]] = relationship(back_populates="run", cascade="all, delete-orphan")


Index("uq_active_run_month", Run.period_month, Run.financial_year, unique=True,
      sqlite_where=(Run.status != "superseded"),
      postgresql_where=(Run.status != "superseded"))


class RunRow(Base):
    """One reportable Day Book line inside a run."""
    __tablename__ = "run_rows"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)

    voucher_id: Mapped[str] = mapped_column(String(60), index=True)
    head: Mapped[str] = mapped_column(String(40))
    year: Mapped[str] = mapped_column(String(12))
    month: Mapped[str] = mapped_column(String(10))
    voucher_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    voucher_type: Mapped[str] = mapped_column(String(12))
    voucher_no: Mapped[str] = mapped_column(String(30))
    bill_no: Mapped[str] = mapped_column(String(60), default="")
    bill_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    project: Mapped[str] = mapped_column(String(120), index=True, default="")
    narration: Mapped[str] = mapped_column(Text, default="")
    account_head: Mapped[str] = mapped_column(String(160), default="")
    account_name: Mapped[str] = mapped_column(String(200), default="")
    ledger: Mapped[str] = mapped_column(String(200), index=True, default="")
    ledger_raw: Mapped[str] = mapped_column(String(200), default="")
    gstin: Mapped[str] = mapped_column(String(20), default="")
    debit: Mapped[float] = mapped_column(Float, default=0)
    credit: Mapped[float] = mapped_column(Float, default=0)
    closing: Mapped[float] = mapped_column(Float, default=0)
    formula_key: Mapped[str] = mapped_column(String(60), default="")
    eligibility: Mapped[str] = mapped_column(String(20), index=True)
    gst_status: Mapped[str] = mapped_column(String(30), index=True)
    rcm: Mapped[bool] = mapped_column(Boolean, default=False)
    party_kind: Mapped[str] = mapped_column(String(40), default="Vendor")
    party_source: Mapped[str] = mapped_column(String(60), default="")
    flags: Mapped[str] = mapped_column(Text, default="")     # comma-separated data-quality flags

    run: Mapped["Run"] = relationship(back_populates="rows")


Index("ix_runrow_run_status", RunRow.run_id, RunRow.gst_status)


class Rectification(Base):
    """A voucher needing an accounts decision, with its resolution."""
    __tablename__ = "rectifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    voucher_id: Mapped[str] = mapped_column(String(60), index=True)
    ledger: Mapped[str] = mapped_column(String(200), default="")
    account_name: Mapped[str] = mapped_column(String(200), default="")
    project: Mapped[str] = mapped_column(String(120), default="")
    amount: Mapped[float] = mapped_column(Float, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    suggestions: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    # open | in_progress | resolved_registered | resolved_unregistered | resolved_exempt | wont_fix
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    assigned_to: Mapped["User"] = relationship(foreign_keys=[assigned_to_id])
    resolved_by: Mapped["User"] = relationship(foreign_keys=[resolved_by_id])
    run: Mapped["Run"] = relationship()


class LedgerOverride(Base):
    """A manual correction to a resolved party name, reused every month.

    Keyed on the raw resolved text so a recurring cash/imprest party keeps its
    correction across runs, per Finding #10 of the specification.
    """
    __tablename__ = "ledger_overrides"
    id: Mapped[int] = mapped_column(primary_key=True)
    raw_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    corrected_ledger: Mapped[str] = mapped_column(String(200))
    party_kind: Mapped[str] = mapped_column(String(40), default="Vendor")
    note: Mapped[str] = mapped_column(Text, default="")
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    updated_by: Mapped["User"] = relationship()


class Creditor(Base):
    """The GSTIN master, held in the application so corrections persist."""
    __tablename__ = "creditors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name_key: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    account_name: Mapped[str] = mapped_column(String(220))
    legal_name: Mapped[str] = mapped_column(String(220), default="")
    group_name: Mapped[str] = mapped_column(String(120), default="")
    gstin: Mapped[str] = mapped_column(String(20), default="")
    pan: Mapped[str] = mapped_column(String(20), default="")
    gstin_valid: Mapped[str] = mapped_column(String(20), default="")   # ok | bad_format | bad_checksum | pan_mismatch
    source: Mapped[str] = mapped_column(String(40), default="import")  # import | manual
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Setting(Base):
    """Editable masters: keyword list, head map, filters, thresholds."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")               # JSON
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AuditLog(Base):
    """Who changed what, when. Never updated or deleted by the application."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    user_label: Mapped[str] = mapped_column(String(160), default="")
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[str] = mapped_column(String(80), default="")
    before: Mapped[str] = mapped_column(Text, default="")
    after: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
