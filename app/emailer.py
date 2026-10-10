"""Owner notification email. Resend (HTTPS API) or SMTP. Never reports success unless the provider did."""
import html, logging, smtplib
from email.message import EmailMessage
import httpx
from .config import settings
from .db import SessionLocal
from .models import Inquiry, now

log = logging.getLogger("gillstudio.email")


def _rows(i: Inquiry):
    d, a = i.data or {}, i.appointment
    ap = "None requested"
    if a:
        ap = f"{a.date} {a.time} ({settings.business_tz_label}) — {a.method} — status: {a.status} (client zone: {a.client_tz or 'n/a'})"
        if a.agenda:
            ap += f" — agenda: {a.agenda}"
    j = lambda v: ", ".join(v) if v else "—"
    return [
        ("Reference", i.ref), ("Submitted (UTC)", i.created_at.strftime("%Y-%m-%d %H:%M")),
        ("Client name", i.name), ("Email", i.email), ("WhatsApp", i.whatsapp),
        ("Preferred contact", i.contact_method), ("Business", i.business or "—"),
        ("Website", d.get("website") or "—"), ("Social profile", d.get("social") or "—"),
        ("Requested service", i.service), ("Selected package", i.package or "—"),
        ("Project type", d.get("projectType", "—")), ("Redesign notes", d.get("redesignNotes") or "—"),
        ("Purpose", d.get("purpose", "—")), ("Audience", d.get("audience") or "—"),
        ("Features", j(d.get("features"))), ("Pages", d.get("pages", "—")),
        ("Domain / hosting", f"{d.get('domain','—')} / {d.get('hosting','—')}"),
        ("Assets owned", j(d.get("assets"))), ("Reference sites", d.get("references") or "—"),
        ("Budget", i.data.get("budget") or "—"), ("Desired date", d.get("dueDate") or "—"),
        ("Urgency", d.get("urgency", "—")), ("Deadline notes", d.get("deadlineNotes") or "—"),
        ("Appointment", ap), ("Additional notes", d.get("details") or "—"),
        ("Marketing consent", "Yes" if i.marketing_consent else "No"), ("Status", i.status),
    ]


def build(i: Inquiry):
    subject = "New Gill Studio Project Inquiry — " + " ".join(i.name.split())
    rows = _rows(i)
    text = "\n".join(f"{k}: {v}" for k, v in rows)
    trs = "".join(
        f'<tr><td style="padding:8px 10px;color:#8a8580;font-size:13px;vertical-align:top;width:34%;border-bottom:1px solid #2a2a2a">{html.escape(k)}</td>'
        f'<td style="padding:8px 10px;color:#f3efec;font-size:14px;border-bottom:1px solid #2a2a2a;word-break:break-word;white-space:pre-wrap">{html.escape(str(v))}</td></tr>'
        for k, v in rows)
    page = (f'<div style="background:#070707;padding:16px"><div style="max-width:640px;margin:auto;background:#101010;border-radius:12px;overflow:hidden">'
            f'<div style="background:#e3162b;color:#fff;padding:14px 18px;font:700 18px Arial">New project inquiry</div>'
            f'<table role="presentation" width="100%" cellspacing="0" style="font-family:Arial,sans-serif">{trs}</table></div></div>')
    return subject, text, page


def _send_resend(subject, text, page, reply_to, ref):
    last = None
    for attempt in (1, 2):
        try:
            r = httpx.post(settings.resend_api_url.rstrip("/") + "/emails", timeout=15,
                           headers={"Authorization": f"Bearer {settings.resend_api_key}",
                                    "Idempotency-Key": f"gs-{ref}"},
                           json={"from": settings.email_from, "to": [settings.notify_email], "subject": subject,
                                 "html": page, "text": text, "reply_to": [reply_to]})
            if r.status_code == 200 and r.json().get("id"):
                return
            last = f"Resend HTTP {r.status_code}: {r.text[:180]}"
            if r.status_code < 500:
                break
        except Exception as e:  # network / timeout
            last = f"{type(e).__name__}: {str(e)[:150]}"
    raise RuntimeError(last)


def _send_smtp(subject, text, page, reply_to, ref):
    m = EmailMessage()
    m["Subject"], m["From"], m["To"], m["Reply-To"] = subject, settings.email_from or settings.smtp_user, settings.notify_email, reply_to
    m.set_content(text)
    m.add_alternative(page, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
        s.starttls()
        s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(m)


def send_notification(inquiry_id: int):
    """Runs after the response (background). Records the real outcome on the inquiry."""
    db = SessionLocal()
    try:
        i = db.get(Inquiry, inquiry_id)
        if not i:
            return
        if not settings.email_configured():
            i.email_status, i.email_error = "not_configured", "Email provider is not configured on the server"
            log.warning("EMAIL NOT CONFIGURED: inquiry %s stored but no notification sent", i.ref)
        else:
            try:
                subject, text, page = build(i)
                (_send_resend if settings.email_provider == "resend" else _send_smtp)(subject, text, page, i.email, i.ref)
                i.email_status, i.email_error, i.email_sent_at = "sent", None, now()
            except Exception as e:
                msg = str(e).replace(settings.resend_api_key or "\0", "***")[:300]
                i.email_status, i.email_error = "failed", msg
                log.error("Email failed for %s: %s", i.ref, msg)
        db.commit()
    finally:
        db.close()
