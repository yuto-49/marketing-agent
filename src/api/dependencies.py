"""Shared application state and dependency accessors.

Separated from main.py to avoid circular imports between the app
module and its routers.
"""

from typing import TYPE_CHECKING, Any, Generator

from sqlalchemy.orm import Session

from src.db.database import get_session

if TYPE_CHECKING:
    from src.explainability.shap_explainer import ShapExplainer
    from src.simulation.engine import SimulationEngine

# Populated during the FastAPI lifespan handler in main.py.
_state: dict[str, Any] = {}


def init_state(engine: "SimulationEngine", explainer: "ShapExplainer") -> None:
    """Called once by the lifespan handler to populate shared state."""
    _state["engine"] = engine
    _state["explainer"] = explainer


def clear_state() -> None:
    """Called on shutdown."""
    _state.clear()


def get_engine() -> "SimulationEngine":
    """Return the shared SimulationEngine instance."""
    return _state["engine"]


def get_explainer() -> "ShapExplainer":
    """Return the shared ShapExplainer instance."""
    return _state["explainer"]


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and closes it after."""
    db = get_session()
    try:
        yield db
    finally:
        db.close()
