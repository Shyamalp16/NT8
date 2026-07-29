"""Statistical utilities shared by the research pipeline.

Phase 5 uses transparent classical intervals for descriptive market-clock
summaries.  Bootstrap and multiple-testing helpers belong to the Phase 6 event
study implementation and are intentionally not applied to Phase 5 findings.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
from scipy.stats import norm, t


def mean_confidence_interval(
    mean: np.ndarray | float,
    standard_deviation: np.ndarray | float,
    observations: np.ndarray | int,
    confidence_level: float = 0.95,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return a two-sided Student-t confidence interval for a sample mean.

    Cells with fewer than two observations or a non-finite standard deviation
    receive ``NaN`` bounds.  Inputs broadcast according to NumPy rules.
    """
    mean_array = np.asarray(mean, dtype=float)
    std_array = np.asarray(standard_deviation, dtype=float)
    n_array = np.asarray(observations, dtype=float)
    alpha = 1.0 - float(confidence_level)
    valid = (n_array >= 2) & np.isfinite(mean_array) & np.isfinite(std_array)
    degrees = np.maximum(n_array - 1.0, 1.0)
    critical = t.ppf(1.0 - alpha / 2.0, degrees)
    margin = critical * std_array / np.sqrt(n_array)
    lower = np.where(valid, mean_array - margin, np.nan)
    upper = np.where(valid, mean_array + margin, np.nan)
    return lower, upper


def wilson_interval(
    successes: np.ndarray | int,
    observations: np.ndarray | int,
    confidence_level: float = 0.95,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return a two-sided Wilson score interval for a binomial proportion."""
    success_array = np.asarray(successes, dtype=float)
    n_array = np.asarray(observations, dtype=float)
    alpha = 1.0 - float(confidence_level)
    z_value = norm.ppf(1.0 - alpha / 2.0)
    valid = (
        (n_array > 0)
        & (success_array >= 0)
        & (success_array <= n_array)
    )
    proportion = np.divide(
        success_array,
        n_array,
        out=np.full_like(success_array, np.nan, dtype=float),
        where=n_array > 0,
    )
    denominator = 1.0 + z_value**2 / n_array
    center = (proportion + z_value**2 / (2.0 * n_array)) / denominator
    half_width = (
        z_value
        * np.sqrt(
            proportion * (1.0 - proportion) / n_array
            + z_value**2 / (4.0 * n_array**2)
        )
        / denominator
    )
    lower = np.where(valid, np.maximum(0.0, center - half_width), np.nan)
    upper = np.where(valid, np.minimum(1.0, center + half_width), np.nan)
    return lower, upper
