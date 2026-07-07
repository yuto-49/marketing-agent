"""Ranking-accuracy metrics for validating simulation predictions against real outcomes.

Headline metrics (per spec):
  - Hit@k: did the real winner appear in the sim's top-k?
  - RankCorr: Spearman correlation between sim ranks and real ranks.
  - Wildcard beat rate: how often does a sim-rejected candidate beat the sim's pick?
  - Calibration beta: regression slope of tau_real on tau_sim (internal gate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class RankingMetrics:
    """Result of comparing sim-predicted rankings against real outcomes."""

    hit_at_1: bool
    hit_at_3: bool
    hit_at_k: bool
    k: int
    rank_corr: float
    rank_corr_pvalue: float
    n_variants: int
    sim_winner: str
    real_winner: str
    wildcard_beat_rate: float | None


@dataclass(frozen=True)
class CalibrationMetrics:
    """Regression of tau_real on tau_sim to assess magnitude calibration."""

    beta: float
    intercept: float
    r_squared: float
    n_observations: int


def compute_ranking_metrics(
    variant_ids: Sequence[str],
    sim_ranks: Sequence[int],
    real_ranks: Sequence[int],
    k: int = 3,
    wildcard_id: str | None = None,
) -> RankingMetrics:
    """Compare sim-predicted ranks against observed real ranks.

    Args:
        variant_ids: Identifiers for each variant.
        sim_ranks: Sim-predicted rank per variant (1 = best).
        real_ranks: Observed real rank per variant (1 = best).
        k: Top-k threshold for Hit@k.
        wildcard_id: Optional sim-rejected variant pushed to live test.

    Returns:
        RankingMetrics with hit rates, correlation, and wildcard analysis.
    """
    if len(variant_ids) != len(sim_ranks) or len(variant_ids) != len(real_ranks):
        raise ValueError("variant_ids, sim_ranks, and real_ranks must have equal length")

    n = len(variant_ids)
    if n < 2:
        raise ValueError("Need at least 2 variants to compute ranking metrics")

    sim_arr = np.array(sim_ranks)
    real_arr = np.array(real_ranks)

    sim_winner_idx = int(np.argmin(sim_arr))
    real_winner_idx = int(np.argmin(real_arr))
    sim_winner = variant_ids[sim_winner_idx]
    real_winner = variant_ids[real_winner_idx]

    # Hit@k: is the real winner in the sim's top-k?
    sim_topk_indices = set(np.argsort(sim_arr)[:k].tolist())
    hit_at_k = real_winner_idx in sim_topk_indices
    hit_at_1 = real_winner_idx == sim_winner_idx
    hit_at_3 = real_winner_idx in set(np.argsort(sim_arr)[:min(3, n)].tolist())

    # Spearman rank correlation
    if n >= 3:
        corr_result = stats.spearmanr(sim_arr, real_arr)
        rank_corr = float(corr_result.statistic)
        rank_corr_pvalue = float(corr_result.pvalue)
    else:
        rank_corr = 1.0 if np.array_equal(sim_arr, real_arr) else -1.0
        rank_corr_pvalue = 1.0

    # Wildcard beat rate
    wildcard_beat_rate: float | None = None
    if wildcard_id is not None and wildcard_id in variant_ids:
        wc_idx = list(variant_ids).index(wildcard_id)
        wc_real_rank = real_ranks[wc_idx]
        wildcard_beat_rate = 1.0 if wc_real_rank < real_ranks[sim_winner_idx] else 0.0

    return RankingMetrics(
        hit_at_1=hit_at_1,
        hit_at_3=hit_at_3,
        hit_at_k=hit_at_k,
        k=k,
        rank_corr=rank_corr,
        rank_corr_pvalue=rank_corr_pvalue,
        n_variants=n,
        sim_winner=sim_winner,
        real_winner=real_winner,
        wildcard_beat_rate=wildcard_beat_rate,
    )


def compute_calibration(
    tau_sim: Sequence[float],
    tau_real: Sequence[float],
) -> CalibrationMetrics:
    """Regress tau_real on tau_sim to assess magnitude calibration.

    A well-calibrated sim has beta close to 1.0 and intercept close to 0.

    Args:
        tau_sim: Predicted treatment effects from simulation.
        tau_real: Observed real treatment effects.

    Returns:
        CalibrationMetrics with slope, intercept, and R-squared.
    """
    n = len(tau_sim)
    if n != len(tau_real):
        raise ValueError("tau_sim and tau_real must have equal length")
    if n < 3:
        raise ValueError("Need at least 3 observations for calibration regression")

    sim_arr = np.array(tau_sim, dtype=float)
    real_arr = np.array(tau_real, dtype=float)

    result = stats.linregress(sim_arr, real_arr)

    return CalibrationMetrics(
        beta=float(result.slope),
        intercept=float(result.intercept),
        r_squared=float(result.rvalue ** 2),
        n_observations=n,
    )


def compute_rolling_accuracy(
    history: Sequence[RankingMetrics],
    alpha: float = 0.3,
) -> dict[str, float]:
    """Compute EWMA-smoothed accuracy metrics over experiment history.

    Args:
        history: Sequence of past RankingMetrics (oldest first).
        alpha: EWMA smoothing factor (higher = more weight on recent).

    Returns:
        Dict with smoothed hit_at_1_rate, hit_at_3_rate, mean_rank_corr.
    """
    if not history:
        return {"hit_at_1_rate": 0.0, "hit_at_3_rate": 0.0, "mean_rank_corr": 0.0}

    hit1_ewma = float(history[0].hit_at_1)
    hit3_ewma = float(history[0].hit_at_3)
    corr_ewma = history[0].rank_corr

    for m in history[1:]:
        hit1_ewma = alpha * float(m.hit_at_1) + (1 - alpha) * hit1_ewma
        hit3_ewma = alpha * float(m.hit_at_3) + (1 - alpha) * hit3_ewma
        corr_ewma = alpha * m.rank_corr + (1 - alpha) * corr_ewma

    return {
        "hit_at_1_rate": round(hit1_ewma, 4),
        "hit_at_3_rate": round(hit3_ewma, 4),
        "mean_rank_corr": round(corr_ewma, 4),
    }
