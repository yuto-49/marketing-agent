"""SQLite database engine and session factory.

Uses synchronous SQLAlchemy with a local SQLite file.  The DB file
lives at ``data/mirofish.db`` relative to the project root.
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_DB_DIR = Path("data")
_DB_PATH = _DB_DIR / "mirofish.db"

_engine = create_engine(
    f"sqlite:///{_DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def init_db() -> None:
    """Create all tables if they don't exist yet."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    from .models import Base

    Base.metadata.create_all(bind=_engine)


def get_session() -> Session:
    """Return a new database session.  Caller is responsible for closing."""
    return SessionLocal()
