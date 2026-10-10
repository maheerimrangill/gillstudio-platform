"""All configuration comes from environment variables (see .env.example)."""
import os


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _db_url():
    url = os.getenv("DATABASE_URL", "sqlite:///./gillstudio.db").strip()
    # Neon/Render give postgres:// or postgresql:// ; SQLAlchemy needs the psycopg driver name.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Settings:
    def __init__(self):
        self.env = os.getenv("APP_ENV", "development").lower()
        self.production = self.env == "production"
        self.database_url = _db_url()
        self.secret_key = os.getenv("SECRET_KEY", "")
        self.admin_username = os.getenv("ADMIN_USERNAME", "admin")
        self.admin_password_hash = os.getenv("ADMIN_PASSWORD_HASH", "")
        self.business_tz = os.getenv("BUSINESS_TZ", "Asia/Karachi")
        self.business_tz_label = os.getenv("BUSINESS_TZ_LABEL", "Pakistan Standard Time (PKT)")
        # email
        self.email_provider = os.getenv("EMAIL_PROVIDER", "none").lower()  # resend | smtp | none
        self.notify_email = os.getenv("NOTIFY_EMAIL", "")
        self.email_from = os.getenv("EMAIL_FROM", "Gill Studio <onboarding@resend.dev>")
        self.resend_api_key = os.getenv("RESEND_API_KEY", "")
        self.resend_api_url = os.getenv("RESEND_API_URL", "https://api.resend.com")
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = _int("SMTP_PORT", 587)
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        # abuse limits
        self.inquiry_limit = _int("INQUIRY_RATE_LIMIT", 5)          # per IP per window
        self.inquiry_window = _int("INQUIRY_RATE_WINDOW_SECONDS", 3600)
        self.login_limit = _int("LOGIN_RATE_LIMIT", 5)              # failures per IP per window
        self.login_window = _int("LOGIN_RATE_WINDOW_SECONDS", 900)
        self.min_fill_ms = _int("MIN_FORM_FILL_MS", 3000)
        self.session_hours = _int("SESSION_HOURS", 8)

    def email_configured(self):
        if self.email_provider == "resend":
            return bool(self.resend_api_key and self.notify_email)
        if self.email_provider == "smtp":
            return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.notify_email)
        return False

    def check_production(self):
        problems = []
        if len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be set to a random string of at least 32 characters")
        if not self.admin_password_hash:
            problems.append("ADMIN_PASSWORD_HASH is not set (run: python -m app.hashpw)")
        if problems:
            raise RuntimeError("Refusing to start in production: " + "; ".join(problems))


settings = Settings()
