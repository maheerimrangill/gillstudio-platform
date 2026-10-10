import re
from datetime import date as Date
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, available_timezones
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PHONE_RE = re.compile(r"^\+\d[\d\s-]{7,16}$")
IDEM_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
YesNo = Literal["Yes", "No", "Not sure yet"]


class Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _clean(cls, v):
        if isinstance(v, str):
            return _CTRL.sub("", v)
        if isinstance(v, list):
            return [_CTRL.sub("", x) if isinstance(x, str) else x for x in v]
        return v


def _url(v):
    if not v:
        return ""
    p = urlparse(v)
    if p.scheme not in ("http", "https") or "." not in (p.netloc or "") or len(v) > 300 or " " in v:
        raise ValueError("Enter a full link starting with http:// or https://")
    return v


class AppointmentIn(Base):
    date: str
    time: str
    method: Literal["Online call", "Phone call", "WhatsApp call"] = "Online call"
    clientTz: str = Field("", max_length=60)
    agenda: str = Field("", max_length=300)

    @field_validator("clientTz")
    @classmethod
    def _tz(cls, v):
        if v and v not in available_timezones():
            return ""          # unknown zone label: ignore rather than reject the booking
        return v


class InquiryIn(Base):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    whatsapp: str = Field(max_length=30)
    contactMethod: Literal["WhatsApp", "Email", "Phone call"]
    business: str = Field("", max_length=150)
    website: str = ""
    social: str = ""
    service: str = Field(min_length=2, max_length=120)
    projectType: Literal["New website", "Redesign of existing site", "Not sure yet"]
    redesignNotes: str = Field("", max_length=1500)
    purpose: str = Field(min_length=10, max_length=2000)
    audience: str = Field("", max_length=300)
    features: list[str] = Field(default_factory=list, max_length=12)
    pages: str = Field("Not sure yet", max_length=30)
    domain: YesNo = "Not sure yet"
    hosting: YesNo = "Not sure yet"
    assets: list[str] = Field(default_factory=list, max_length=8)
    references: str = Field("", max_length=1500)
    details: str = Field("", max_length=3000)
    package: str = Field("", max_length=80)
    budget: str = Field("", max_length=60)
    dueDate: str = ""
    urgency: Literal["Flexible", "Within a month", "Within two weeks", "Urgent"] = "Flexible"
    deadlineNotes: str = Field("", max_length=300)
    appointment: AppointmentIn | None = None
    marketingConsent: bool = False
    consent: bool
    idempotencyKey: str
    formStartedAt: int | None = None
    website_hp: str = ""

    @field_validator("whatsapp")
    @classmethod
    def _wa(cls, v):
        if not PHONE_RE.match(v) or not 8 <= len(re.sub(r"\D", "", v)) <= 15:
            raise ValueError("Include the country code, e.g. +92 3XX XXXXXXX")
        return v

    @field_validator("website", "social")
    @classmethod
    def _urls(cls, v):
        return _url(v)

    @field_validator("features", "assets")
    @classmethod
    def _lists(cls, v):
        if any(not isinstance(x, str) or len(x) > 40 for x in v):
            raise ValueError("Invalid selection")
        return v

    @field_validator("dueDate")
    @classmethod
    def _due(cls, v):
        if not v:
            return ""
        try:
            d = Date.fromisoformat(v)
        except ValueError:
            raise ValueError("Enter a valid date")
        if d < Date.today().replace(year=Date.today().year - 1):
            raise ValueError("Enter a valid date")
        return v

    @field_validator("consent")
    @classmethod
    def _consent(cls, v):
        if v is not True:
            raise ValueError("Consent is required to send your inquiry")
        return v

    @field_validator("idempotencyKey")
    @classmethod
    def _idem(cls, v):
        if not IDEM_RE.match(v):
            raise ValueError("Invalid request key")
        return v


# ---- admin ----
class LoginIn(Base):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


class StatusIn(Base):
    status: Literal["New", "Contacted", "Consultation Requested", "Confirmed", "Proposal Sent",
                    "In Progress", "Completed", "Declined"]


class NoteIn(Base):
    text: str = Field(min_length=1, max_length=2000)


class SlotIn(Base):
    date: str
    time: str
