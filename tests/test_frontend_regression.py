"""Regression tests for the public frontend (static/index.html)."""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text(encoding="utf-8")


def _visible_text(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    return re.sub(r"<[^>]+>", " ", html)


def test_home_serves_new_frontend(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Start your project" in r.text and "Starting packages" not in r.text


def test_no_pricing_anywhere():
    text = _visible_text(HTML)
    assert not re.search(r"pricing|starting (at|from)|packages?\b|budget", text, re.I)
    assert not re.search(r"\$\s?\d|\bUSD\b|\bRs\.?\s?\d|PKR\s?\d", text, re.I)
    assert 'id="pricing"' not in HTML and 'href="#pricing"' not in HTML


def test_frontend_uses_only_real_api_endpoints(client):
    # app.openapi() lists the registered routes even though the docs UI is disabled
    paths = client.app.openapi()["paths"]
    real = {p for p in paths if p.startswith("/api/")}
    assert "post" in paths["/api/inquiries"] and "get" in paths["/api/availability"]
    used = set(re.findall(r"/api/[A-Za-z0-9_/-]+", HTML))
    assert used, "frontend should call the API"
    assert used <= real, f"unknown endpoints used: {used - real}"
    assert used == {"/api/inquiries", "/api/availability"}
    assert not re.search(r"[\"'(=]/admin", HTML)   # no link or call into the admin app


def test_no_unexpected_external_scripts():
    assert not re.search(r"<script[^>]+src=", HTML, re.I)
    hosts = set(re.findall(r"https?://([^/\"' )<]+)", HTML))
    assert hosts <= {"fonts.googleapis.com", "fonts.gstatic.com", "wa.me", "www.instagram.com", "."}


def test_payload_fields_are_accepted_by_backend_schema():
    """Every key the form sends must exist on InquiryIn (otherwise the backend silently drops it)."""
    from app.schemas import InquiryIn
    block = re.search(r"p=\{name:v\.name.*?website_hp:\$\(\"#hp\"\)\.value\}", HTML, re.S).group(0)
    sent = set(re.findall(r"(?:^|[{,])([A-Za-z_]+):", block))
    assert sent <= set(InquiryIn.model_fields), sent - set(InquiryIn.model_fields)