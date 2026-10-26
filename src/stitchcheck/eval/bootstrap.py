"""Cluster bootstrap for confidence intervals.

Photos of the same sewn pair are correlated, so phone-set intervals resample whole pairs, not photos.
"""
import numpy as np
import pandas as pd


def cluster_bootstrap(df, metric, group="pair_id", n_boot=2000, alpha=0.05, seed=0):
    """Point estimate and percentile CI of metric(df), resampling groups with replacement.

    metric: function DataFrame -> float (may return nan, e.g. recall with no positives; those draws are dropped).
    Returns dict(value, lo, hi, n_rows, n_groups, n_boot_valid).
    """
    rng = np.random.default_rng(seed)
    groups = {k: g for k, g in df.groupby(group, sort=True)}
    keys = list(groups)
    draws = []
    for _ in range(n_boot):
        sample = pd.concat([groups[keys[i]] for i in rng.integers(0, len(keys), len(keys))], ignore_index=True)
        v = metric(sample)
        if not np.isnan(v):
            draws.append(v)
    lo, hi = (np.quantile(draws, [alpha / 2, 1 - alpha / 2]) if draws else (np.nan, np.nan))
    return dict(value=float(metric(df)), lo=float(lo), hi=float(hi), n_rows=len(df), n_groups=len(keys),
                n_boot_valid=len(draws))


def recall(df, positive, y_true="label", y_pred="pred"):
    """Share of rows whose true label is in `positive` that were predicted as any label in `positive`."""
    pos = df[df[y_true].isin(positive)]
    return float(pos[y_pred].isin(positive).mean()) if len(pos) else float("nan")
