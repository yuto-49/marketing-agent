"""Synthetic population generator for LINE marketing simulation.

Generates or loads synthetic agent populations with demographic and
behavioural attributes sampled from configurable distributions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


# Top prefectures by population (2024 estimates, descending order).
_TOP_PREFECTURES: list[str] = [
    "東京都",
    "神奈川県",
    "大阪府",
    "愛知県",
    "埼玉県",
    "千葉県",
    "兵庫県",
    "北海道",
    "福岡県",
    "静岡県",
]

# Default prefecture weights (roughly proportional to population share).
_PREFECTURE_WEIGHTS: list[float] = [
    0.18, 0.12, 0.11, 0.10, 0.09,
    0.08, 0.07, 0.07, 0.06, 0.12,
]

# Common LINE OA tag vocabulary for synthetic data.
_TAG_POOL: list[str] = [
    "クーポン利用済み",
    "リッチメニュー閲覧",
    "アンケート回答済み",
    "ECサイト訪問",
    "店舗来店",
    "友だち紹介",
    "キャンペーン参加",
    "メッセージ既読",
    "カード連携済み",
    "ポイント会員",
]


@dataclass(frozen=True)
class SegmentParams:
    """Immutable container for segment distribution parameters."""

    version: str
    segment_id: str
    segment_name: str
    population_size: int
    distributions: dict[str, Any]


class SyntheticPopulation:
    """Generate or load synthetic agent populations for LINE marketing simulation.

    Each agent is a dictionary with the following keys:
        - agent_id: str
        - age_band: str
        - gender: str
        - prefecture: str
        - days_since_friend_add: int
        - past_open_rate: float
        - past_click_rate: float
        - engagement_level: str
        - tag_set: list[str]
    """

    def __init__(self, segment_params_path: Path) -> None:
        """Load segment parameters from a versioned JSON file.

        Args:
            segment_params_path: Path to the segment parameter JSON file.

        Raises:
            FileNotFoundError: If the parameter file does not exist.
            KeyError: If required keys are missing from the JSON.
        """
        raw = json.loads(segment_params_path.read_text(encoding="utf-8"))
        self._params = SegmentParams(
            version=raw["version"],
            segment_id=raw["segment_id"],
            segment_name=raw["segment_name"],
            population_size=raw["population_size"],
            distributions=raw["distributions"],
        )

    @property
    def params(self) -> SegmentParams:
        """Return the loaded segment parameters."""
        return self._params

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate(self, n: int, seed: int = 42) -> list[dict[str, Any]]:
        """Generate *n* synthetic agents based on segment parameter distributions.

        Args:
            n: Number of agents to generate.
            seed: Random seed for reproducibility.

        Returns:
            List of agent attribute dictionaries.
        """
        rng = np.random.default_rng(seed)
        dists = self._params.distributions

        age_bands = _sample_categorical(rng, dists["age_band"], n)
        genders = _sample_categorical(rng, dists["gender"], n)
        prefectures = _sample_categorical_from_lists(
            rng, _TOP_PREFECTURES, _PREFECTURE_WEIGHTS, n,
        )
        days_since = _sample_continuous(rng, dists["days_since_friend_add"], n)
        open_rates = _sample_continuous(rng, dists["past_open_rate"], n)
        click_rates = _sample_continuous(rng, dists["past_click_rate"], n)
        engagement_levels = _sample_categorical(rng, dists["engagement_level"], n)
        tag_sets = _sample_tags(rng, _TAG_POOL, n)

        agents: list[dict[str, Any]] = []
        seg_id = self._params.segment_id
        for i in range(n):
            agents.append({
                "agent_id": f"{seg_id}_agent_{i:06d}",
                "age_band": age_bands[i],
                "gender": genders[i],
                "prefecture": prefectures[i],
                "days_since_friend_add": int(np.clip(days_since[i], 1, 1800)),
                "past_open_rate": float(np.clip(open_rates[i], 0.0, 1.0)),
                "past_click_rate": float(np.clip(click_rates[i], 0.0, 1.0)),
                "engagement_level": engagement_levels[i],
                "tag_set": tag_sets[i],
            })
        return agents

    # ------------------------------------------------------------------
    # Alternative constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_dataframe(cls, df: Any) -> "SyntheticPopulation":
        """Create a population instance from a pandas DataFrame of real agent data.

        The DataFrame must contain columns matching the agent attribute names.
        Distributions are inferred from the data and written to a temporary
        in-memory parameter set.

        Args:
            df: A ``pandas.DataFrame`` with columns for each agent attribute.

        Returns:
            A ``SyntheticPopulation`` instance with inferred distributions.
        """
        import tempfile

        distributions: dict[str, Any] = {}

        # Categorical attributes -----------------------------------------
        for col in ("age_band", "gender", "engagement_level"):
            if col in df.columns:
                counts = df[col].value_counts(normalize=True)
                distributions[col] = {str(k): float(v) for k, v in counts.items()}

        # Continuous: days_since_friend_add (lognormal fit) ---------------
        if "days_since_friend_add" in df.columns:
            log_vals = np.log(df["days_since_friend_add"].clip(lower=1).values.astype(float))
            distributions["days_since_friend_add"] = {
                "distribution": "lognormal",
                "mean": float(np.mean(log_vals)),
                "sigma": float(np.std(log_vals)),
            }

        # Continuous: beta-distributed rates ------------------------------
        for col in ("past_open_rate", "past_click_rate"):
            if col in df.columns:
                vals = df[col].clip(0.001, 0.999).values.astype(float)
                mean_val = float(np.mean(vals))
                var_val = float(np.var(vals))
                # Method-of-moments for Beta distribution
                if var_val > 0:
                    common = mean_val * (1 - mean_val) / var_val - 1
                    alpha = float(mean_val * common)
                    beta_param = float((1 - mean_val) * common)
                else:
                    alpha, beta_param = 2.0, 5.0
                distributions[col] = {
                    "distribution": "beta",
                    "alpha": alpha,
                    "beta": beta_param,
                }

        params_dict = {
            "version": "1.0.0",
            "segment_id": "seg_from_df",
            "segment_name": "DataFrame由来セグメント",
            "population_size": len(df),
            "distributions": distributions,
        }

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8",
        )
        json.dump(params_dict, tmp, ensure_ascii=False)
        tmp.flush()
        tmp.close()
        return cls(Path(tmp.name))


# ======================================================================
# Private helpers
# ======================================================================

def _sample_categorical(
    rng: np.random.Generator,
    dist: dict[str, float],
    n: int,
) -> list[str]:
    """Sample *n* values from a categorical distribution dict."""
    categories = list(dist.keys())
    probabilities = np.array([dist[c] for c in categories], dtype=float)
    probabilities /= probabilities.sum()  # normalise
    indices = rng.choice(len(categories), size=n, p=probabilities)
    return [categories[i] for i in indices]


def _sample_categorical_from_lists(
    rng: np.random.Generator,
    categories: list[str],
    weights: list[float],
    n: int,
) -> list[str]:
    """Sample *n* values from explicit category/weight lists."""
    probabilities = np.array(weights, dtype=float)
    probabilities /= probabilities.sum()
    indices = rng.choice(len(categories), size=n, p=probabilities)
    return [categories[i] for i in indices]


def _sample_continuous(
    rng: np.random.Generator,
    dist: dict[str, Any],
    n: int,
) -> np.ndarray:
    """Sample *n* values from a continuous distribution specification."""
    dist_type = dist["distribution"]
    if dist_type == "lognormal":
        return rng.lognormal(mean=dist["mean"], sigma=dist["sigma"], size=n)
    if dist_type == "beta":
        return rng.beta(a=dist["alpha"], b=dist["beta"], size=n)
    raise ValueError(f"Unsupported distribution type: {dist_type}")


def _sample_tags(
    rng: np.random.Generator,
    tag_pool: list[str],
    n: int,
    max_tags: int = 4,
) -> list[list[str]]:
    """Assign a random subset of tags to each agent."""
    result: list[list[str]] = []
    for _ in range(n):
        k = int(rng.integers(0, max_tags + 1))
        chosen = rng.choice(tag_pool, size=k, replace=False).tolist() if k > 0 else []
        result.append(sorted(chosen))
    return result
