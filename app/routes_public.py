import logging, secrets, time
from datetime import timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from . import availability as av
from .config import settings
from .db import get_db
from .emailer import send_notification
from .models import Appointment, Inquiry, now
from .schemas import InquiryIn
from .security import client_ip, ip_hash, limiter

log = logging.getLogger("gillstudio")
router = APIRouter(prefix="/api")
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _ref():
    return "GS-" + now().strftime("%y%m%d") + "-" + "".join(secrets.choice(ALPHABET) for _ in range(4))


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("select 1"))
    return {"ok": True}


@router.get("/availability")
def availability(date: str | None = None, db: Session = Depends(get_db)):
    cfg = av.load(db)
    base = {"timezone": settings.business_tz, "tzLabel": settings.business_tz_label}
    if date is None:   # summary used by the form for hints
        return {**base, "days": cfg["days"], "slots": cfg["slots"], "minNoticeHours": cfg["min_notice_hours"],
                "maxDaysAhead": cfg["max_days_ahead"]}
    d = av.parse_date(date)
    if not d:
        raise HTTPException(422, "Invalid date")
    # first slot decides whether the whole date is closed (day/blocked/range/past) — report why
    reasons = {av.check_slot(cfg, date, s) for s in cfg["slots"]}
    open_slots = []
    for s in cfg["slots"]:
        if av.check_slot(cfg, date, s) is None:
            open_slots.append({"time": s, "available": av.slot_state(db, cfg, date, s) == "open"})
    reason = None
    if not open_slots:
        reason = next((r for r in sorted(reasons, key=lambda x: x or "") if r), "No slots available on that date.")
    return {**base, "date": date, "slots": open_slots, "reason": reason}


@router.post("/inquiries", status_code=201)
def create_inquiry(body: InquiryIn, request: Request, bg: BackgroundTasks, db: Session = Depends(get_db)):
    ip = client_ip(request)
    limiter.hit("inq:" + ip, settings.inquiry_limit, settings.inquiry_window)

    if body.website_hp:                      # honeypot: only bots fill this; pretend success, store nothing
        return {"ok": True, "reference": "GS-" + now().strftime("%y%m%d") + "-0000", "appointment": None}
    if body.formStartedAt and time.time() * 1000 - body.formStartedAt < settings.min_fill_ms:
        raise HTTPException(400, "Please review your answers and try again.")

    # idempotency: retries / double clicks return the original record, never a second one
    existing = db.scalar(select(Inquiry).where(Inquiry.idem_key == body.idempotencyKey))
    if not existing:   # near-duplicate: same email + same purpose within 10 minutes
        existing = db.scalar(select(Inquiry).where(
            Inquiry.email == str(body.email).lower(), Inquiry.created_at > now() - timedelta(minutes=10)))
        if existing and (existing.data or {}).get("purpose") != body.purpose:
            existing = None
    if existing:
        return JSONResponse({"ok": True, "reference": existing.ref, "duplicate": True,
                             "appointment": {"status": existing.appointment.status} if existing.appointment else None},
                            status_code=200)

    cfg = av.load(db)
    ap = body.appointment
    if ap:
        err = av.check_slot(cfg, ap.date, ap.time)
        if err:
            raise HTTPException(422, detail={"errors": {"appointment.date": err}})
        if av.slot_state(db, cfg, ap.date, ap.time) != "open":
            return JSONResponse({"ok": False, "code": "slot_unavailable",
                                 "message": "That time slot was just taken. Please choose another."}, status_code=409)

    data = body.model_dump(exclude={"name", "email", "whatsapp", "contactMethod", "business", "service", "package",
                                     "appointment", "marketingConsent", "consent", "idempotencyKey",
                                     "formStartedAt", "website_hp"})
    for attempt in range(5):
        inq = Inquiry(ref=_ref(), idem_key=body.idempotencyKey, name=body.name, email=str(body.email).lower(),
                      whatsapp=body.whatsapp, contact_method=body.contactMethod, business=body.business,
                      service=body.service, package=body.package, data=data, marketing_consent=body.marketingConsent,
                      status="Consultation Requested" if ap else "New", ip_hash=ip_hash(ip))
        if ap:
            inq.appointment = Appointment(date=ap.date, time=ap.time, client_tz=ap.clientTz, method=ap.method,
                                          agenda=ap.agenda, status="requested")
        db.add(inq)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            again = db.scalar(select(Inquiry).where(Inquiry.idem_key == body.idempotencyKey))
            if again:   # concurrent duplicate won the race
                return JSONResponse({"ok": True, "reference": again.ref, "duplicate": True}, status_code=200)
    else:
        raise HTTPException(500, "Could not save your inquiry. Please try again.")

    bg.add_task(send_notification, inq.id)
    return {"ok": True, "reference": inq.ref, "appointment": {"status": "requested"} if ap else None}
