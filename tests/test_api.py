import itertools
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.exc import IntegrityError
from conftest import RESEND
from app.db import SessionLocal
from app.models import Appointment, Inquiry
from app import config
from app.security import limiter

_n = itertools.count(1)


def good_date(offset=4):
    d = datetime.now(ZoneInfo("Asia/Karachi")).date() + timedelta(days=offset)
    while (d.weekday() + 1) % 7 == 0:        # skip Sunday (closed by default)
        d += timedelta(days=1)
    return d.isoformat()


def payload(**kw):
    n = next(_n)
    p = {"name": "Ayesha Khan", "email": f"ayesha{n}@example.com", "whatsapp": "+92 300 1234567",
         "contactMethod": "WhatsApp", "business": "Aroma Kitchen", "website": "", "social": "", "service": "Business / company website",
         "projectType": "New website", "purpose": f"A website for our restaurant, request {n}", "features": ["Gallery"],
         "package": "Starter Website", "budget": "$80 – $150", "consent": True, "idempotencyKey": f"key{n:020d}",
         "formStartedAt": 1, "website_hp": ""}
    p.update(kw)
    return p


def appt(time="12:00", date=None, **kw):
    return {"date": date or good_date(), "time": time, "method": "Online call", "clientTz": "Asia/Karachi", "agenda": "Scope", **kw}


# ---------------- public ----------------
def test_health_and_static(client):
    assert client.get("/api/health").json() == {"ok": True}
    r = client.get("/")
    assert r.status_code == 200 and "Gill" in r.text and "/api/inquiries" in r.text
    assert client.get("/nope").status_code == 404 and "404" in client.get("/nope").text
    assert "Disallow: /admin" in client.get("/robots.txt").text
    assert client.get("/").headers["x-frame-options"] == "DENY"


def test_availability_rules(client):
    s = client.get("/api/availability").json()
    assert s["timezone"] == "Asia/Karachi" and 0 not in s["days"]
    j = client.get("/api/availability", params={"date": good_date()}).json()
    assert [x["time"] for x in j["slots"]][:2] == ["11:00", "12:00"] and all(x["available"] for x in j["slots"])
    past = client.get("/api/availability", params={"date": "2020-01-06"}).json()
    assert past["slots"] == [] and past["reason"]
    d = datetime.now(ZoneInfo("Asia/Karachi")).date() + timedelta(days=5)
    while (d.weekday() + 1) % 7 != 0:
        d += timedelta(days=1)
    sunday = client.get("/api/availability", params={"date": d.isoformat()}).json()
    assert sunday["slots"] == [] and "day" in sunday["reason"].lower()
    assert client.get("/api/availability", params={"date": "garbage"}).status_code == 422


def test_submit_persists_and_emails(client):
    p = payload(appointment=appt(), name="Zed <script>alert(1)</script>")
    r = client.post("/api/inquiries", json=p)
    assert r.status_code == 201, r.text
    ref = r.json()["reference"]
    assert ref.startswith("GS-") and r.json()["appointment"]["status"] == "requested"
    with SessionLocal() as db:
        i = db.query(Inquiry).filter_by(ref=ref).one()
        assert i.email == p["email"] and i.status == "Consultation Requested"
        assert i.appointment.status == "requested" and i.data["purpose"] == p["purpose"]
        assert i.email_status == "sent" and i.email_error is None
    call = RESEND["calls"][-1]
    assert call["path"] == "/emails" and call["auth"] == "Bearer re_test_key" and call["idem"] == f"gs-{ref}"
    b = call["body"]
    assert b["subject"].startswith("New Gill Studio Project Inquiry — Zed") and b["to"] == ["owner@example.com"]
    assert b["reply_to"] == [p["email"]] and ref in b["text"] and "12:00" in b["text"] and "Starter Website" in b["text"]
    assert "<script>" not in b["html"] and "&lt;script&gt;" in b["html"]          # client text is escaped


@pytest.mark.parametrize("field,value", [("email", "not-an-email"), ("whatsapp", "03001234567"), ("name", "A"),
    ("purpose", "short"), ("consent", False), ("contactMethod", "Telegram"), ("website", "javascript:alert(1)"),
    ("idempotencyKey", "bad key!")])
def test_validation_rejects(client, field, value):
    r = client.post("/api/inquiries", json=payload(**{field: value}))
    assert r.status_code == 422 and r.json()["errors"], r.text
    with SessionLocal() as db:
        assert db.query(Inquiry).filter_by(email=payload()["email"]).count() == 0


def test_missing_required_and_bad_slots(client):
    r = client.post("/api/inquiries", json={"name": "Only Name"})
    assert r.status_code == 422 and "email" in r.json()["errors"]
    for a, key in [(appt(date="2020-01-06"), "appointment.date"), (appt(time="03:00"), "appointment.date"),
                   (appt(date=(datetime.now().date()).isoformat(), time="11:00"), "appointment.date")]:
        r = client.post("/api/inquiries", json=payload(appointment=a))
        assert r.status_code == 422 and key in r.json()["errors"], (a, r.text)


def test_spam_controls(client):
    r = client.post("/api/inquiries", json=payload(website_hp="http://spam"))
    assert r.status_code == 201 and RESEND["calls"] == []                          # honeypot: nothing stored/sent
    import time as _t
    config.settings.min_fill_ms = 3000
    try:
        r = client.post("/api/inquiries", json=payload(formStartedAt=int(_t.time() * 1000)))
        assert r.status_code == 400
    finally:
        config.settings.min_fill_ms = 0
    limiter.reset()                                                                # earlier spam attempts also count
    codes = [client.post("/api/inquiries", json=payload()).status_code for _ in range(7)]
    assert codes[:5] == [201] * 5 and codes[5:] == [429, 429]                      # per-IP rate limit (5/hour)


def test_duplicates(client):
    p = payload()
    a = client.post("/api/inquiries", json=p).json()
    b = client.post("/api/inquiries", json=p)                                      # same idempotency key (double click)
    assert b.status_code == 200 and b.json()["reference"] == a["reference"] and b.json()["duplicate"]
    c = client.post("/api/inquiries", json={**p, "idempotencyKey": "other" + "0" * 20})   # new key, same email+purpose
    assert c.json()["reference"] == a["reference"]
    with SessionLocal() as db:
        assert db.query(Inquiry).filter_by(email=p["email"]).count() == 1
    assert len(RESEND["calls"]) == 1


def test_email_failure_is_reported_not_faked(client):
    RESEND["status"] = 500
    r = client.post("/api/inquiries", json=payload())
    assert r.status_code == 201                                                    # inquiry is still saved
    with SessionLocal() as db:
        i = db.query(Inquiry).order_by(Inquiry.id.desc()).first()
        assert i.email_status == "failed" and "500" in i.email_error and "re_test_key" not in i.email_error


def test_email_not_configured(client):
    config.settings.email_provider = "none"
    client.post("/api/inquiries", json=payload())
    with SessionLocal() as db:
        i = db.query(Inquiry).order_by(Inquiry.id.desc()).first()
        assert i.email_status == "not_configured" and RESEND["calls"] == []


# ---------------- admin security ----------------
def test_admin_requires_auth(client):
    for path in ["/admin/api/inquiries", "/admin/api/inquiries/GS-X", "/admin/api/availability", "/admin/api/export.csv", "/admin/api/me"]:
        assert client.get(path).status_code == 401, path
    assert client.post("/admin/api/inquiries/GS-X/notes", json={"text": "x"}).status_code == 401
    assert client.put("/admin/api/availability", json={}).status_code == 401
    assert client.get("/admin").status_code == 200                                 # only the login page is public
    assert "gs_admin" not in client.cookies


def test_login_and_csrf_and_bruteforce(client):
    assert client.post("/admin/api/login", json={"username": "maheer", "password": "wrong"}).status_code == 401
    assert client.post("/admin/api/login", json={"username": "root", "password": "correct-horse-battery"}).status_code == 401
    r = client.post("/admin/api/login", json={"username": "maheer", "password": "correct-horse-battery"})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert client.get("/admin/api/inquiries").status_code == 200
    assert client.post("/admin/api/logout").status_code == 200                      # no CSRF needed to log out
    limiter.reset()
    codes = [client.post("/admin/api/login", json={"username": "maheer", "password": "bad"}).status_code for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
    assert client.post("/admin/api/login", json={"username": "maheer", "password": "correct-horse-battery"}).status_code == 429


def test_mutation_needs_csrf_token(client):
    limiter.reset()
    client.post("/admin/api/login", json={"username": "maheer", "password": "correct-horse-battery"})
    client.post("/api/inquiries", json=payload())
    ref = client.get("/admin/api/inquiries").json()["items"][0]["ref"]
    assert client.patch(f"/admin/api/inquiries/{ref}/status", json={"status": "Contacted"}).status_code == 403
    assert client.patch(f"/admin/api/inquiries/{ref}/status", json={"status": "Contacted"}, headers={"X-CSRF-Token": "forged"}).status_code == 403


# ---------------- admin workflow + double booking ----------------
def test_admin_workflow(admin):
    d = good_date()
    ref = admin.post("/api/inquiries", json=payload(appointment=appt(date=d), business="Zeta Co")).json()["reference"]
    lst = admin.get("/admin/api/inquiries", params={"q": "zeta"}).json()
    assert lst["total"] == 1 and lst["items"][0]["ref"] == ref
    assert admin.get("/admin/api/inquiries", params={"status": "Completed"}).json()["total"] == 0
    assert admin.get("/admin/api/inquiries", params={"q": "%"}).json()["total"] == 0          # wildcard is escaped
    assert admin.get("/admin/api/inquiries/GS-NOPE").status_code == 404
    det = admin.get(f"/admin/api/inquiries/{ref}").json()
    assert det["data"]["purpose"] and det["appointment"]["status"] == "requested"
    assert admin.patch(f"/admin/api/inquiries/{ref}/status", json={"status": "Bogus"}).status_code == 422
    assert admin.patch(f"/admin/api/inquiries/{ref}/status", json={"status": "Contacted"}).json()["status"] == "Contacted"
    n = admin.post(f"/admin/api/inquiries/{ref}/notes", json={"text": "Called, wants logo too"}).json()["notes"]
    assert n[-1]["text"] == "Called, wants logo too" and not n[-1]["system"]
    c = admin.post(f"/admin/api/inquiries/{ref}/appointment/confirm")
    assert c.status_code == 200 and c.json()["appointment"]["status"] == "confirmed" and c.json()["status"] == "Confirmed"
    assert admin.post(f"/admin/api/inquiries/{ref}/appointment/confirm").json()["appointment"]["status"] == "confirmed"  # idempotent
    csv = admin.get("/admin/api/export.csv")
    assert csv.status_code == 200 and ref in csv.text and csv.headers["content-type"].startswith("text/csv")


def test_double_booking_prevented(admin):
    d = good_date(6)
    r1 = admin.post("/api/inquiries", json=payload(appointment=appt("14:00", d))).json()["reference"]
    r2 = admin.post("/api/inquiries", json=payload(appointment=appt("14:00", d)))          # second *request* allowed (pending)
    assert r2.status_code == 201
    r2 = r2.json()["reference"]
    assert admin.post(f"/admin/api/inquiries/{r1}/appointment/confirm").status_code == 200
    # slot is now hidden from the public and cannot be requested again
    slots = {x["time"]: x["available"] for x in admin.get("/api/availability", params={"date": d}).json()["slots"]}
    assert slots["14:00"] is False and slots["15:00"] is True
    late = admin.post("/api/inquiries", json=payload(appointment=appt("14:00", d)))
    assert late.status_code == 409 and late.json()["code"] == "slot_unavailable"
    # the other pending request cannot be confirmed into the same slot (server check)
    bad = admin.post(f"/admin/api/inquiries/{r2}/appointment/confirm")
    assert bad.status_code == 409
    assert admin.get(f"/admin/api/inquiries/{r2}").json()["appointment"]["status"] == "requested"   # never marked confirmed
    assert admin.post(f"/admin/api/inquiries/{r2}/appointment/reschedule", json={"date": d, "time": "14:00"}).status_code == 409
    ok = admin.post(f"/admin/api/inquiries/{r2}/appointment/reschedule", json={"date": d, "time": "16:00"})
    assert ok.status_code == 200 and ok.json()["appointment"]["time"] == "16:00" and ok.json()["appointment"]["status"] == "confirmed"
    assert admin.post(f"/admin/api/inquiries/{r2}/appointment/reschedule", json={"date": d, "time": "09:00"}).status_code == 422
    # declining frees the slot again
    admin.post(f"/admin/api/inquiries/{r1}/appointment/decline")
    slots = {x["time"]: x["available"] for x in admin.get("/api/availability", params={"date": d}).json()["slots"]}
    assert slots["14:00"] is True


def test_database_itself_refuses_double_confirmed(client):
    d = good_date(8)
    refs = [client.post("/api/inquiries", json=payload(appointment=appt("17:00", d))).json()["reference"] for _ in range(2)]
    with SessionLocal() as db:
        a, b = [db.query(Inquiry).filter_by(ref=r).one().appointment for r in refs]
        a.status = "confirmed"; db.commit()
        b.status = "confirmed"
        with pytest.raises(IntegrityError):
            db.commit()                                                                  # raw DB constraint, bypassing app code


def test_availability_admin_edit(admin):
    cur = admin.get("/admin/api/availability").json()
    assert admin.put("/admin/api/availability", json={**cur, "slots": ["25:00"]}).status_code == 422
    assert admin.put("/admin/api/availability", json={**cur, "days": [9]}).status_code == 422
    d = good_date(9)
    admin.put("/admin/api/availability", json={**cur, "blocked_dates": [d], "slots": ["10:00", "13:00"]})
    j = admin.get("/api/availability", params={"date": d}).json()
    assert j["slots"] == [] and "not available" in j["reason"].lower()
    admin.put("/admin/api/availability", json=cur)                                       # restore
    j = admin.get("/api/availability", params={"date": d}).json()
    assert len(j["slots"]) == len(cur["slots"])


def test_csv_formula_injection_guard(admin):
    admin.post("/api/inquiries", json=payload(name="=HYPERLINK(\"http://evil\")"))
    assert "'=HYPERLINK" in admin.get("/admin/api/export.csv").text
