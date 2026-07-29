"""Statistical utilities shared by the research pipeline.

Phase 5 uses transparent classical intervals for descriptive market-clock
summaries.  Bootstrap and multiple-testing helpers belong to the Phase 6 event
study implementation and are intentionally not applied to Phase 5 findings.
"""

from __future__ import annotations

from typing import Sequence, Tuple

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


def bootstrap_mean_interval(
    values: Sequence[float] | np.ndarray,
    *,
    confidence_level: float = 0.95,
    replicates: int = 5_000,
    random_seed: int | np.random.Generator = 0,
) -> tuple[float, float]:
    """Return a percentile bootstrap interval for a one-row-per-session mean."""
    sample = np.asarray(values, dtype=float)
    sample = sample[np.isfinite(sample)]
    if sample.size == 0:
        return float("nan"), float("nan")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between zero and one")
    if int(replicates) <= 0:
        raise ValueError("replicates must be positive")
    generator = (
        random_seed
        if isinstance(random_seed, np.random.Generator)
        else np.random.default_rng(int(random_seed))
    )
    indices = generator.integers(
        0,
        sample.size,
        size=(int(replicates), sample.size),
    )
    means = sample[indices].mean(axis=1)
    alpha = 1.0 - float(confidence_level)
    lower, upper = np.quantile(means, [alpha / 2.0, 1.0 - alpha / 2.0])
    return float(lower), float(upper)


def clustered_two_group_difference_interval(
    values: Sequence[float] | np.ndarray,
    groups: Sequence[float] | np.ndarray,
    clusters: Sequence[object] | np.ndarray,
    *,
    confidence_level: float = 0.95,
    replicates: int = 5_000,
    random_seed: int | np.random.Generator = 0,
) -> tuple[float, float]:
    """Bootstrap a group-one minus group-zero mean difference by cluster.

    Every resampled cluster retains all of its rows and group assignments.
    This supports event studies with several prespecified observations from the
    same session without treating those observations as independent.
    """
    sample = np.asarray(values, dtype=float)
    labels = np.asarray(groups, dtype=float)
    cluster_labels = np.asarray(clusters)
    valid = (
        np.isfinite(sample)
        & np.isfinite(labels)
        & np.isin(labels, [0.0, 1.0])
    )
    sample = sample[valid]
    labels = labels[valid].astype(np.int8)
    cluster_labels = cluster_labels[valid]
    if sample.size == 0 or np.unique(labels).size != 2:
        return float("nan"), float("nan")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between zero and one")
    if int(replicates) <= 0:
        raise ValueError("replicates must be positive")

    _, cluster_codes = np.unique(cluster_labels, return_inverse=True)
    cluster_count = int(cluster_codes.max()) + 1
    group_one = labels == 1
    group_zero = ~group_one
    one_sum = np.bincount(
        cluster_codes[group_one],
        weights=sample[group_one],
        minlength=cluster_count,
    )
    one_count = np.bincount(
        cluster_codes[group_one],
        minlength=cluster_count,
    )
    zero_sum = np.bincount(
        cluster_codes[group_zero],
        weights=sample[group_zero],
        minlength=cluster_count,
    )
    zero_count = np.bincount(
        cluster_codes[group_zero],
        minlength=cluster_count,
    )
    generator = (
        random_seed
        if isinstance(random_seed, np.random.Generator)
        else np.random.default_rng(int(random_seed))
    )
    indices = generator.integers(
        0,
        cluster_count,
        size=(int(replicates), cluster_count),
    )
    sampled_one_count = one_count[indices].sum(axis=1)
    sampled_zero_count = zero_count[indices].sum(axis=1)
    valid_replicates = (sampled_one_count > 0) & (sampled_zero_count > 0)
    differences = (
        one_sum[indices].sum(axis=1)[valid_replicates]
        / sampled_one_count[valid_replicates]
        - zero_sum[indices].sum(axis=1)[valid_replicates]
        / sampled_zero_count[valid_replicates]
    )
    if differences.size == 0:
        return float("nan"), float("nan")
    alpha = 1.0 - float(confidence_level)
    lower, upper = np.quantile(
        differences,
        [alpha / 2.0, 1.0 - alpha / 2.0],
    )
    return float(lower), float(upper)


def stratified_label_permutation_pvalue(
    values: Sequence[float] | np.ndarray,
    groups: Sequence[float] | np.ndarray,
    strata: Sequence[object] | np.ndarray,
    *,
    replicates: int = 5_000,
    random_seed: int | np.random.Generator = 0,
    alternative: str = "two_sided",
) -> float:
    """Test a two-group mean difference by permuting labels within strata."""
    sample = np.asarray(values, dtype=float)
    labels = np.asarray(groups, dtype=float)
    stratum_labels = np.asarray(strata)
    valid = (
        np.isfinite(sample)
        & np.isfinite(labels)
        & np.isin(labels, [0.0, 1.0])
    )
    sample = sample[valid]
    labels = labels[valid].astype(np.int8)
    stratum_labels = stratum_labels[valid]
    if sample.size == 0 or np.unique(labels).size != 2:
        return float("nan")
    generator = (
        random_seed
        if isinstance(random_seed, np.random.Generator)
        else np.random.default_rng(int(random_seed))
    )
    observed = float(sample[labels == 1].mean() - sample[labels == 0].mean())
    stratum_positions = [
        np.flatnonzero(stratum_labels == label)
        for label in np.unique(stratum_labels)
    ]
    null_differences = np.empty(int(replicates), dtype=float)
    for replicate in range(int(replicates)):
        permuted = labels.copy()
        for positions in stratum_positions:
            permuted[positions] = generator.permutation(labels[positions])
        null_differences[replicate] = (
            sample[permuted == 1].mean() - sample[permuted == 0].mean()
        )
    return _randomization_pvalue(
        observed,
        null_differences,
        alternative=alternative,
    )


def sign_flip_permutation_pvalue(
    values: Sequence[float] | np.ndarray,
    *,
    replicates: int = 5_000,
    random_seed: int | np.random.Generator = 0,
    alternative: str = "two_sided",
) -> float:
    """Test a session-level mean against zero with a Rademacher null."""
    sample = np.asarray(values, dtype=float)
    sample = sample[np.isfinite(sample)]
    if sample.size == 0:
        return float("nan")
    generator = (
        random_seed
        if isinstance(random_seed, np.random.Generator)
        else np.random.default_rng(int(random_seed))
    )
    observed = float(sample.mean())
    signs = generator.integers(
        0,
        2,
        size=(int(replicates), sample.size),
        dtype=np.int8,
    )
    null_means = ((signs * 2 - 1) * sample).mean(axis=1)
    return _randomization_pvalue(
        observed,
        null_means,
        alternative=alternative,
    )


def direction_permutation_pvalue(
    directions: Sequence[float] | np.ndarray,
    forward_returns: Sequence[float] | np.ndarray,
    *,
    replicates: int = 5_000,
    random_seed: int | np.random.Generator = 0,
    alternative: str = "two_sided",
) -> float:
    """Test direction/return association by permuting directions by session."""
    direction = np.asarray(directions, dtype=float)
    returns = np.asarray(forward_returns, dtype=float)
    valid = np.isfinite(direction) & np.isfinite(returns) & (direction != 0)
    direction = np.sign(direction[valid])
    returns = returns[valid]
    if direction.size == 0:
        return float("nan")
    generator = (
        random_seed
        if isinstance(random_seed, np.random.Generator)
        else np.random.default_rng(int(random_seed))
    )
    observed = float(np.mean(direction * returns))
    null_means = np.empty(int(replicates), dtype=float)
    for index in range(int(replicates)):
        null_means[index] = np.mean(generator.permutation(direction) * returns)
    return _randomization_pvalue(
        observed,
        null_means,
        alternative=alternative,
    )


def benjamini_hochberg(
    p_values: Sequence[float] | np.ndarray,
    *,
    alpha: float = 0.05,
) -> tuple[np.ndarray, np.ndarray]:
    """Return BH-adjusted q-values and rejection flags in original order."""
    values = np.asarray(p_values, dtype=float)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be between zero and one")
    adjusted = np.full(values.shape, np.nan, dtype=float)
    rejected = np.zeros(values.shape, dtype=bool)
    valid_positions = np.flatnonzero(np.isfinite(values))
    if valid_positions.size == 0:
        return adjusted, rejected
    valid_values = values[valid_positions]
    if ((valid_values < 0.0) | (valid_values > 1.0)).any():
        raise ValueError("p-values must be between zero and one")
    order = np.argsort(valid_values, kind="mergesort")
    ranked = valid_values[order]
    count = ranked.size
    raw_adjusted = ranked * count / np.arange(1, count + 1, dtype=float)
    monotone = np.minimum.accumulate(raw_adjusted[::-1])[::-1]
    monotone = np.minimum(monotone, 1.0)
    restored = np.empty_like(monotone)
    restored[order] = monotone
    adjusted[valid_positions] = restored
    rejected[valid_positions] = restored <= float(alpha)
    return adjusted, rejected


def _randomization_pvalue(
    observed: float,
    null_values: np.ndarray,
    *,
    alternative: str,
) -> float:
    if int(null_values.size) <= 0:
        raise ValueError("At least one randomization replicate is required")
    if alternative == "two_sided":
        exceedances = np.count_nonzero(np.abs(null_values) >= abs(observed))
    elif alternative == "greater":
        exceedances = np.count_nonzero(null_values >= observed)
    elif alternative == "less":
        exceedances = np.count_nonzero(null_values <= observed)
    else:
        raise ValueError(f"Unknown alternative: {alternative}")
    return float((1 + exceedances) / (1 + null_values.size))
