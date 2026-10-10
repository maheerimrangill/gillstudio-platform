"""Availability rules. Stored in the DB (editable from the admin dashboard); defaults below."""
import re
from datetime import date as Date, datetime, time as Time, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import func, select
from .config import settings
from .models import Appointment, Setting, now

DEFAULTS = {
    "days": [1, 2, 3, 4, 5, 6],                # 0 = Sunday ... 6 = Saturday (same as JavaScript)
    "slots": ["11:00", "12:00", "14:00", "15:00", "16:00", "17:00"],
    "min_notice_hours": 24,
    "max_days_ahead": 45,
    "blocked_dates": [],
    "max_pending_per_slot": 2,                 # soft cap on unconfirmed requests for one slot
}
SLOT_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
tz = lambda: ZoneInfo(settings.business_tz)


def load(db):
    row = db.get(Setting, "availability")
    return {**DEFAULTS, **(row.value if row else {})}


def save(db, cfg):
    row = db.get(Setting, "availability")
    if row:
        row.value = cfg
    else:
        db.add(Setting(key="availability", value=cfg))


def parse_date(s):
    try:
        return Date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def start_dt(d: Date, t: str):
    h, m = map(int, t.split(":"))
    return datetime.combine(d, Time(h, m), tzinfo=tz())


def check_slot(cfg, date_s, time_s, admin=False):
    """Return an error message, or None if the slot is bookable under the rules."""
    d = parse_date(date_s)
    if not d:
        return "Choose a valid date."
    if not time_s or not SLOT_RE.match(time_s) or time_s not in cfg["slots"]:
        return "Choose one of the listed time slots."
    if date_s in cfg["blocked_dates"]:
        return "We are not available on that date."
    if ((d.weekday() + 1) % 7) not in cfg["days"]:
        return "We don't take consultations on that day."
    start = start_dt(d, time_s)
    n = now().astimezone(tz())
    if start <= n:
        return "That time has already passed."
    if not admin and start < n + timedelta(hours=cfg["min_notice_hours"]):
        return f"Please choose a time at least {cfg['min_notice_hours']} hours from now."
    if d > n.date() + timedelta(days=cfg["max_days_ahead"]):
        return f"Bookings open up to {cfg['max_days_ahead']} days ahead."
    return None


def slot_state(db, cfg, date_s, time_s, ignore_inquiry_id=None):
    """'confirmed' | 'full' | 'open' for a slot."""
    q = select(Appointment.status, func.count()).where(
        Appointment.date == date_s, Appointment.time == time_s,
        Appointment.status.in_(["confirmed", "requested"]))
    if ignore_inquiry_id:
        q = q.where(Appointment.inquiry_id != ignore_inquiry_id)
    counts = dict(db.execute(q.group_by(Appointment.status)).all())
    if counts.get("confirmed"):
        return "confirmed"
    if counts.get("requested", 0) >= cfg["max_pending_per_slot"]:
        return "full"
    return "open"


def validate_config(c):
    """Validate an admin-submitted availability config; returns (clean, error)."""
    try:
        days = sorted({int(x) for x in c["days"]})
        slots = sorted({str(x) for x in c["slots"]})
        out = {
            "days": days, "slots": slots,
            "min_notice_hours": int(c["min_notice_hours"]),
            "max_days_ahead": int(c["max_days_ahead"]),
            "blocked_dates": sorted({str(x) for x in c.get("blocked_dates", [])}),
            "max_pending_per_slot": int(c["max_pending_per_slot"]),
        }
    except (KeyError, TypeError, ValueError):
        return None, "Invalid availability data."
    if not days or any(d < 0 or d > 6 for d in days):
        return None, "Days must be numbers 0 (Sun) to 6 (Sat), at least one."
    if not slots or len(slots) > 24 or not all(SLOT_RE.match(s) for s in slots):
        return None, "Slots must be HH:MM (24h), between 1 and 24 of them."
    if not 0 <= out["min_notice_hours"] <= 720 or not 1 <= out["max_days_ahead"] <= 180:
        return None, "Notice must be 0-720 hours and window 1-180 days."
    if not 1 <= out["max_pending_per_slot"] <= 10:
        return None, "Pending cap must be 1-10."
    if len(out["blocked_dates"]) > 200 or not all(parse_date(x) for x in out["blocked_dates"]):
        return None, "Blocked dates must be YYYY-MM-DD (max 200)."
    return out, None
