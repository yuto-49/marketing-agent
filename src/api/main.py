"""FastAPI application for the MiroFish simulation API."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .dependencies import clear_state, init_state
from .routers import comparison, explanation, simulation

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize simulation engine and explainer on startup."""
    from src.explainability.shap_explainer import ShapExplainer
    from src.simulation.engine import SimulationEngine

    params_dir = Path("src/data/segment_params")
    engine = SimulationEngine(params_dir=params_dir)
    explainer = ShapExplainer(engine.response_model)

    init_state(engine, explainer)

    logger.info("Simulation engine initialised (params_dir=%s)", params_dir)
    yield
    clear_state()
    logger.info("Application state cleared")


app = FastAPI(
    title="MiroFish Simulation API",
    description="LINE marketing campaign simulation with explainable predictions",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(simulation.router, prefix="/api/simulations", tags=["simulations"])
app.include_router(explanation.router, prefix="/api/simulations", tags=["explanation"])
app.include_router(comparison.router, prefix="/api/simulations", tags=["comparison"])


