"""Database engine and session handling."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool
from .config import DATABASE_URL, ON_VERCEL

connect_args = {}
engine_options = {"pool_pre_ping": True, "future": True}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
elif ON_VERCEL:
    # Serverless: open a connection per request and let the Supabase pooler manage them.
    engine_options["poolclass"] = NullPool
else:
    # Render can recycle idle Postgres connections during deploys or maintenance.
    # Recycle them proactively and fail promptly instead of hanging a request.
    engine_options.update(pool_recycle=300, pool_timeout=30)

engine = create_engine(DATABASE_URL, connect_args=connect_args, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
