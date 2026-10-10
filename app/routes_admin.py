import csv, io
from datetime import timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from . import availability as av
from .config import settings
from .db import get_db
from .emailer import send_notification
from .models import Appointment, Inquiry, Note, now
from .schemas import LoginIn, NoteIn, SlotIn, StatusIn
from .security import (COOKIE, check_login, client_ip, limiter, make_session, read_session, require_admin)

router = APIRouter(prefix="/admin/api")
auth = [Depends(require_admin)]
iso = lambda d: d.astimezone(timezone.utc).isoformat() if d else None


def _appt(a):
    return None if not a else {"date": a.date, "time": a.time, "status": a.status, "method": a.method,
                               "clientTz": a.client_tz, "agenda": a.agenda, "decidedAt": iso(a.decided_at)}


def _summary(i):
    return {"ref": i.ref, "createdAt": iso(i.created_at), "name": i.name, "email": i.email, "service": i.service,
            "package": i.package, "status": i.status, "emailStatus": i.email_status, "appointment": _appt(i.appointment)}


def _detail(i):
    return {**_summary(i), "whatsapp": i.whatsapp, "contactMethod": i.contact_method, "business": i.business,
            "data": i.data, "marketingConsent": i.marketing_consent, "emailError": i.email_error,
            "emailSentAt": iso(i.email_sent_at), "tzLabel": settings.business_tz_label,
            "notes": [{"at": iso(n.created_at), "text": n.text, "system": n.system} for n in i.notes]}


def _get(db, ref):
    i = db.scalar(select(Inquiry).where(Inquiry.ref == ref))
    if not i:
        raise HTTPException(404, "Inquiry not found")
    return i


def _note(i, text, system=True):
    i.notes.append(Note(text=text, system=system))


# ---------- session ----------
@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    key = "login:" + client_ip(request)
    if limiter.count(key, settings.login_window) >= settings.login_limit:
        raise HTTPException(429, "Too many failed attempts. Try again later.",
                            headers={"Retry-After": str(settings.login_window)})
    if not check_login(body.username, body.password):
        limiter.add(key)
        raise HTTPException(401, "Invalid username or password")
    tok, csrf = make_session()
    response.set_cookie(COOKIE, tok, max_age=settings.session_hours * 3600, httponly=True,
                        secure=settings.production, samesite="strict", path="/")
    return {"ok": True, "csrf": csrf}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    s = read_session(request)
    if not s:
        raise HTTPException(401, "Not authenticated")
    return {"user": s["u"], "csrf": s["csrf"]}


# ---------- inquiries ----------
@router.get("/inquiries", dependencies=auth)
def list_inquiries(q: str = "", status: str = "", page: int = 1, per: int = 25, db: Session = Depends(get_db)):
    per, page = max(1, min(per, 100)), max(1, page)
    stmt = select(Inquiry)
    if status:
        stmt = stmt.where(Inquiry.status == status)
    if q.strip():
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(*[c.ilike(like, escape="\\") for c in
                                (Inquiry.name, Inquiry.email, Inquiry.ref, Inquiry.business, Inquiry.whatsapp)]))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Inquiry.id.desc()).limit(per).offset((page - 1) * per)).all()
    return {"total": total, "page": page, "per": per, "items": [_summary(i) for i in rows]}


@router.get("/inquiries/{ref}", dependencies=auth)
def get_inquiry(ref: str, db: Session = Depends(get_db)):
    return _detail(_get(db, ref))


@router.patch("/inquiries/{ref}/status", dependencies=auth)
def set_status(ref: str, body: StatusIn, db: Session = Depends(get_db)):
    i = _get(db, ref)
    old, i.status = i.status, body.status
    _note(i, f"Status changed: {old} → {body.status}")
    db.commit()
    return _detail(i)


@router.post("/inquiries/{ref}/notes", dependencies=auth)
def add_note(ref: str, body: NoteIn, db: Session = Depends(get_db)):
    i = _get(db, ref)
    _note(i, body.text, system=False)
    db.commit()
    return _detail(i)


@router.post("/inquiries/{ref}/resend-email", dependencies=auth)
def resend_email(ref: str, db: Session = Depends(get_db)):
    i = _get(db, ref)
    send_notification(i.id)
    db.refresh(i)
    return _detail(i)


# ---------- appointments: a booking is 'confirmed' only after the DB commit succeeds ----------
def _book(db, i, date_s, time_s, verb):
    a = i.appointment
    if not a:
        raise HTTPException(409, "This inquiry has no appointment request")
    cfg = av.load(db)
    err = av.check_slot(cfg, date_s, time_s, admin=True)
    if err:
        raise HTTPException(422, err)
    clash = db.scalar(select(Appointment).where(Appointment.date == date_s, Appointment.time == time_s,
                                                Appointment.status == "confirmed", Appointment.id != a.id))
    if clash:
        raise HTTPException(409, "Another booking is already confirmed for that slot")
    old = f"{a.date} {a.time}"
    a.date, a.time, a.status, a.decided_at = date_s, time_s, "confirmed", now()
    i.status = "Confirmed"
    _note(i, f"Appointment {verb}: {date_s} {time_s} ({settings.business_tz_label})" +
          (f" (was {old})" if verb == "rescheduled" else ""))
    try:
        db.commit()                        # unique partial index is the final arbiter under concurrency
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That slot was booked by someone else a moment ago")
    return _detail(i)


@router.post("/inquiries/{ref}/appointment/confirm", dependencies=auth)
def confirm(ref: str, db: Session = Depends(get_db)):
    i = _get(db, ref)
    a = i.appointment
    if a and a.status == "confirmed":
        return _detail(i)
    if a and a.status == "declined":
        raise HTTPException(409, "Declined appointments must be rescheduled to a new slot")
    return _book(db, i, a.date if a else "", a.time if a else "", "confirmed")


@router.post("/inquiries/{ref}/appointment/reschedule", dependencies=auth)
def reschedule(ref: str, body: SlotIn, db: Session = Depends(get_db)):
    return _book(db, _get(db, ref), body.date, body.time, "rescheduled")


@router.post("/inquiries/{ref}/appointment/decline", dependencies=auth)
def decline(ref: str, db: Session = Depends(get_db)):
    i = _get(db, ref)
    if not i.appointment:
        raise HTTPException(409, "This inquiry has no appointment request")
    i.appointment.status, i.appointment.decided_at = "declined", now()
    _note(i, f"Appointment declined: {i.appointment.date} {i.appointment.time}")
    db.commit()
    return _detail(i)


# ---------- availability ----------
@router.get("/availability", dependencies=auth)
def get_avail(db: Session = Depends(get_db)):
    return {**av.load(db), "tzLabel": settings.business_tz_label}


@router.put("/availability", dependencies=auth)
def put_avail(body: dict, db: Session = Depends(get_db)):
    clean, err = av.validate_config(body)
    if err:
        raise HTTPException(422, err)
    av.save(db, clean)
    db.commit()
    return clean


# ---------- export ----------
def _safe(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s   # spreadsheet formula-injection guard


@router.get("/export.csv", dependencies=auth)
def export(db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["reference", "created_utc", "status", "name", "email", "whatsapp", "business", "service", "package",
                "budget", "project_type", "purpose", "appointment_date", "appointment_time", "appointment_status",
                "email_status"])
    for i in db.scalars(select(Inquiry).order_by(Inquiry.id)):
        a, d = i.appointment, i.data or {}
        w.writerow([_safe(x) for x in (i.ref, iso(i.created_at), i.status, i.name, i.email, i.whatsapp, i.business,
                                       i.service, i.package, d.get("budget"), d.get("projectType"), d.get("purpose"),
                                       a and a.date, a and a.time, a and a.status, i.email_status)])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="gill-studio-inquiries.csv"'})
