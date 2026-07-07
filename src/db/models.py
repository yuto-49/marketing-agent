"""SQLAlchemy ORM models for MiroFish persistence.

Tables:
  - simulations: stores simulation metadata + full JSON result
  - segment_params_versions: versioned segment parameter snapshots
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SimulationRecord(Base):
    """Persisted simulation run."""

    __tablename__ = "simulations"

    simulation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    segment_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    n_variants: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_simulations: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    recommended_variant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SegmentParamsVersion(Base):
    """Versioned snapshot of segment parameters for reproducibility."""

    __tablename__ = "segment_params_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    segment_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc)
    )
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="initial"
    )
    tau_real_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
