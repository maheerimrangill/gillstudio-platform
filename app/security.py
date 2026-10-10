import base64, hashlib, hmac, os, secrets, threading, time
from collections import defaultdict, deque
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from .config import settings

COOKIE = "gs_admin"


# ---------- password hashing (stdlib scrypt, no extra dependency) ----------
def hash_password(password: str) -> str:
    n, r, p = 2 ** 14, 8, 1
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    b = lambda x: base64.urlsafe_b64encode(x).decode().rstrip("=")
    return f"scrypt:{n}:{r}:{p}:{b(salt)}:{b(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split(":")
        pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        calc = hashlib.scrypt(password.encode(), salt=pad(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, pad(dk))
    except Exception:
        return False


_DUMMY = hash_password(secrets.token_hex(8))   # used so wrong usernames cost the same time as wrong passwords


# ---------- sessions ----------
def _ser():
    return URLSafeTimedSerializer(settings.secret_key or "dev-only-insecure-key", salt="gs-admin-session")


def make_session():
    csrf = secrets.token_urlsafe(24)
    return _ser().dumps({"u": settings.admin_username, "csrf": csrf}), csrf


def read_session(request: Request):
    tok = request.cookies.get(COOKIE)
    if not tok:
        return None
    try:
        return _ser().loads(tok, max_age=settings.session_hours * 3600)
    except BadSignature:
        return None


def require_admin(request: Request):
    s = read_session(request)
    if not s:
        raise HTTPException(401, "Not authenticated")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent, s["csrf"]):
            raise HTTPException(403, "Bad CSRF token")
    return s


def check_login(username: str, password: str) -> bool:
    ok_user = hmac.compare_digest(username.encode(), settings.admin_username.encode())
    stored = settings.admin_password_hash or _DUMMY
    ok_pw = verify_password(password, stored if ok_user else _DUMMY)
    return bool(ok_user and ok_pw and settings.admin_password_hash)


# ---------- in-memory rate limiter (per process; fine for one free instance) ----------
class RateLimiter:
    def __init__(self):
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def reset(self):
        with self.lock:
            self.hits.clear()

    def count(self, key, window):
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > window:
                q.popleft()
            return len(q)

    def add(self, key):
        with self.lock:
            self.hits[key].append(time.monotonic())

    def hit(self, key, limit, window):
        """Count one hit; raise 429 if over the limit."""
        if self.count(key, window) >= limit:
            raise HTTPException(429, "Too many requests", headers={"Retry-After": str(window)})
        self.add(key)


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def ip_hash(ip: str) -> str:
    return hashlib.sha256((ip + settings.secret_key).encode()).hexdigest()
