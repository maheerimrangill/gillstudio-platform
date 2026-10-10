from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings

_kw = {"pool_pre_ping": True, "pool_recycle": 300}   # Neon suspends idle compute; reconnect transparently
if settings.database_url.startswith("sqlite"):
    _kw = {"connect_args": {"check_same_thread": False}}
engine = create_engine(settings.database_url, **_kw)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401
    Base.metadata.create_all(engine)
