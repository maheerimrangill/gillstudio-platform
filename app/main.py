import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from .config import settings
from .db import init_db
from .routes_admin import router as admin_router
from .routes_public import router as public_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gillstudio")
STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app):
    if settings.production:
        settings.check_production()
    init_db()
    if not settings.email_configured():
        log.warning("DEVELOPMENT WARNING: email is NOT configured (EMAIL_PROVIDER=%s). Inquiries will be stored but "
                    "no notification email will be sent.", settings.email_provider)
    if not settings.admin_password_hash:
        log.warning("ADMIN_PASSWORD_HASH is not set: admin login is disabled.")
    yield


app = FastAPI(title="Gill Studio", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

CSP_SITE = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'")
CSP_ADMIN = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
             "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


@app.middleware("http")
async def guard(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > 64 * 1024:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    resp = await call_next(request)
    p = request.url.path
    admin = p.startswith("/admin")
    resp.headers["Content-Security-Policy"] = CSP_ADMIN if admin else CSP_SITE
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.production:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    if admin or p.startswith("/api"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(RequestValidationError)
async def on_validation(request, exc):
    errors = {}
    for e in exc.errors():
        loc = [str(x) for x in e["loc"] if x not in ("body", "query")]
        errors.setdefault(".".join(loc) or "form", e["msg"].removeprefix("Value error, "))
    return JSONResponse({"detail": "Please check the highlighted answers.", "errors": errors}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def on_http(request, exc):
    if exc.status_code == 404 and not request.url.path.startswith(("/api", "/admin/api")):
        return HTMLResponse(NOT_FOUND, status_code=404)
    d = exc.detail
    if isinstance(d, dict):
        return JSONResponse({"detail": "Please check the highlighted answers.", **d}, status_code=exc.status_code,
                            headers=exc.headers)
    return JSONResponse({"detail": d}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(Exception)
async def on_error(request, exc):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Something went wrong on our side. Please try again."}, status_code=500)


app.include_router(public_router)
app.include_router(admin_router)
app.mount("/admin/static", StaticFiles(directory=STATIC / "admin"), name="admin-static")

NOT_FOUND = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Page not found — Gill Studio</title><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#070707;color:#f3efec;font-family:system-ui;text-align:center">
<div><h1 style="font:400 4rem Impact,sans-serif;color:#e3162b;margin:0">404</h1><p>That page doesn’t exist.</p>
<a href="/" style="color:#ff4455">Back to Gill Studio</a></div>"""


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(STATIC / "admin" / "index.html")


@app.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    return PlainTextResponse(f"User-agent: *\nDisallow: /admin\nDisallow: /api\nAllow: /\nSitemap: {request.base_url}sitemap.xml\n")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    return PlainTextResponse(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                             f'<url><loc>{request.base_url}</loc></url></urlset>', media_type="application/xml")
