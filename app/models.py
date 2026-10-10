from datetime import datetime, timezone
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def now():
    return datetime.now(timezone.utc)


STATUSES = ["New", "Contacted", "Consultation Requested", "Confirmed", "Proposal Sent",
            "In Progress", "Completed", "Declined"]


class Inquiry(Base):
    __tablename__ = "inquiries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    idem_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    status: Mapped[str] = mapped_column(String(30), default="New", index=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254), index=True)
    whatsapp: Mapped[str] = mapped_column(String(30))
    contact_method: Mapped[str] = mapped_column(String(20))
    business: Mapped[str] = mapped_column(String(150), default="")
    service: Mapped[str] = mapped_column(String(120), default="")
    package: Mapped[str] = mapped_column(String(80), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)      # every remaining answer
    marketing_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ip_hash: Mapped[str] = mapped_column(String(64), default="")
    email_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|failed|not_configured
    email_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    appointment: Mapped["Appointment | None"] = relationship(back_populates="inquiry", uselist=False,
                                                             cascade="all, delete-orphan")
    notes: Mapped[list["Note"]] = relationship(back_populates="inquiry", cascade="all, delete-orphan",
                                               order_by="Note.id")


class Appointment(Base):
    """date/time are wall-clock values in the BUSINESS time zone."""
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inquiry_id: Mapped[int] = mapped_column(ForeignKey("inquiries.id", ondelete="CASCADE"), unique=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    time: Mapped[str] = mapped_column(String(5))
    client_tz: Mapped[str] = mapped_column(String(60), default="")
    method: Mapped[str] = mapped_column(String(30), default="Online call")
    agenda: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(12), default="requested")  # requested|confirmed|declined
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    inquiry: Mapped[Inquiry] = relationship(back_populates="appointment")
    __table_args__ = (
        # THE double-booking guard: the database itself refuses two confirmed bookings in one slot.
        Index("uq_confirmed_slot", "date", "time", unique=True,
              sqlite_where=text("status = 'confirmed'"), postgresql_where=text("status = 'confirmed'")),
    )


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inquiry_id: Mapped[int] = mapped_column(ForeignKey("inquiries.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    text: Mapped[str] = mapped_column(Text)
    system: Mapped[bool] = mapped_column(Boolean, default=False)
    inquiry: Mapped[Inquiry] = relationship(back_populates="notes")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
