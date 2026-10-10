import json, os, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest

# ---- mock Resend HTTP API (verifies our request; it is NOT real delivery) ----
RESEND = {"status": 200, "calls": []}


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        RESEND["calls"].append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body,
                                "idem": self.headers.get("Idempotency-Key")})
        self.send_response(RESEND["status"]); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"id": "mock-id"} if RESEND["status"] == 200 else {"message": "boom"}).encode())

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
TMP = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=os.environ.get("TEST_DATABASE_URL", f"sqlite:///{TMP}/t.db"), SECRET_KEY="x" * 40, APP_ENV="development",
                  EMAIL_PROVIDER="resend", RESEND_API_KEY="re_test_key", NOTIFY_EMAIL="owner@example.com",
                  RESEND_API_URL=f"http://127.0.0.1:{srv.server_port}", MIN_FORM_FILL_MS="0")
from app.security import hash_password  # noqa: E402
os.environ["ADMIN_PASSWORD_HASH"] = hash_password("correct-horse-battery")
os.environ["ADMIN_USERNAME"] = "maheer"
from app import config  # noqa: E402
config.settings.__init__()
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.security import limiter  # noqa: E402


@pytest.fixture()
def client():
    limiter.reset(); RESEND["status"] = 200; RESEND["calls"].clear()
    config.settings.email_provider = "resend"
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin(client):
    r = client.post("/admin/api/login", json={"username": "maheer", "password": "correct-horse-battery"})
    assert r.status_code == 200
    client.headers["X-CSRF-Token"] = r.json()["csrf"]
    return client
