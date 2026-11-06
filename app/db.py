"""Database engine and session handling."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool
from .config import DATABASE_URL, ON_VERCEL

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine_options = {}
if ON_VERCEL and not DATABASE_URL.startswith("sqlite"):
    # Serverless: open a connection per request and let the Supabase pooler manage them.
    engine_options["poolclass"] = NullPool
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True, connect_args=connect_args, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
