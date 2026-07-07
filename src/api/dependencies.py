"""Shared application state and dependency accessors.

Separated from main.py to avoid circular imports between the app
module and its routers.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.explainability.shap_explainer import ShapExplainer
    from src.simulation.engine import SimulationEngine

# Populated during the FastAPI lifespan handler in main.py.
_state: dict[str, Any] = {}


def init_state(engine: "SimulationEngine", explainer: "ShapExplainer") -> None:
    """Called once by the lifespan handler to populate shared state."""
    _state["engine"] = engine
    _state["explainer"] = explainer
    _state["results"] = {}


def clear_state() -> None:
    """Called on shutdown."""
    _state.clear()


def get_engine() -> "SimulationEngine":
    """Return the shared SimulationEngine instance."""
    return _state["engine"]


def get_explainer() -> "ShapExplainer":
    """Return the shared ShapExplainer instance."""
    return _state["explainer"]


def get_results_store() -> dict:
    """Return the in-memory results store (simulation_id -> result)."""
    return _state["results"]
