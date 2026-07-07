"""MiroFish marketing simulation engine.

Provides Monte Carlo simulation of marketing funnels with segment-based
parameterisation, synthetic population generation, and confidence-ranked
variant comparison.
"""

from .engine import SimulationEngine
from .schemas import SimulationInput, SimulationResult

__all__ = ["SimulationEngine", "SimulationInput", "SimulationResult"]
