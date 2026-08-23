"""Statistical analysis (proposal Section 10.2).

  * paired bootstrap confidence intervals - every method is evaluated on the
    same frozen trajectory, so the pairing is real and must be exploited
  * McNemar's exact test for paired binary wrong-patient/unauthorized outcomes
  * Brier score and expected calibration error for candidate/influence
    probabilities
  * safety-utility-privacy frontier extraction rather than a single cherry-picked
    operating point
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass
class BootstrapCI:
    metric: str
    condition_a: str
    condition_b: str
    mean_difference: float
    ci_low: float
    ci_high: float
    n_pairs: int
    significant: bool

    def describe(self) -> str:
        verdict = "significant" if self.significant else "not significant"
        return (f"{self.condition_a} - {self.condition_b} on {self.metric}: "
                f"{self.mean_difference:+.4f} "
                f"[{self.ci_low:+.4f}, {self.ci_high:+.4f}] ({verdict})")


def paired_bootstrap(
    metrics: Sequence[Any],
    metric_name: str,
    condition_a: str,
    condition_b: str,
    *,
    n_resamples: int = 10000,
    alpha: float = 0.05,
    seed: int = 20260729,
) -> Optional[BootstrapCI]:
    """Paired bootstrap CI on the per-incident difference A - B."""
    by_incident: Dict[str, Dict[str, Any]] = {}
    for m in metrics:
        by_incident.setdefault(m.incident_id, {})[m.condition] = m

    diffs = [
        getattr(per[condition_a], metric_name) - getattr(per[condition_b], metric_name)
        for per in by_incident.values()
        if condition_a in per and condition_b in per
    ]
    if not diffs:
        return None

    values = np.asarray(diffs, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(n_resamples, len(values)))
    means = values[idx].mean(axis=1)
    low, high = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])

    return BootstrapCI(
        metric=metric_name, condition_a=condition_a, condition_b=condition_b,
        mean_difference=float(values.mean()), ci_low=float(low), ci_high=float(high),
        n_pairs=len(values), significant=bool(low > 0 or high < 0),
    )


# ======================================================================
@dataclass
class McNemarResult:
    condition_a: str
    condition_b: str
    b: int          # A correct, B wrong
    c: int          # A wrong, B correct
    p_value: float
    n_pairs: int

    def describe(self) -> str:
        return (f"McNemar {self.condition_a} vs {self.condition_b}: "
                f"b={self.b} c={self.c} p={self.p_value:.4g} (n={self.n_pairs})")


def mcnemar_exact(metrics: Sequence[Any], condition_a: str, condition_b: str,
                  *, predicate: str = "no_residual_harm") -> Optional[McNemarResult]:
    """Exact McNemar test on paired binary outcomes.

    `predicate` defaults to "did this condition leave zero residual
    wrong-patient/unauthorized harm on this incident".
    """
    def outcome(m) -> bool:
        if predicate == "no_residual_harm":
            return m.rwh == 0.0
        if predicate == "full_recall":
            return m.descendant_recall >= 1.0
        if predicate == "no_exposure":
            return m.uer == 0.0
        raise ValueError(f"unknown predicate {predicate}")

    by_incident: Dict[str, Dict[str, Any]] = {}
    for m in metrics:
        by_incident.setdefault(m.incident_id, {})[m.condition] = m

    b = c = n = 0
    for per in by_incident.values():
        if condition_a not in per or condition_b not in per:
            continue
        n += 1
        a_ok, b_ok = outcome(per[condition_a]), outcome(per[condition_b])
        if a_ok and not b_ok:
            b += 1
        elif b_ok and not a_ok:
            c += 1
    if n == 0:
        return None

    # Exact two-sided binomial test on the discordant pairs.
    total = b + c
    if total == 0:
        p = 1.0
    else:
        k = min(b, c)
        tail = sum(math.comb(total, i) for i in range(0, k + 1)) * (0.5 ** total)
        p = min(1.0, 2.0 * tail)

    return McNemarResult(condition_a, condition_b, b, c, p, n)


# ======================================================================
def brier_score(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    """Mean squared error of probabilistic candidate/influence predictions."""
    if not probabilities:
        return float("nan")
    p = np.clip(np.asarray(probabilities, dtype=float), 0.0, 1.0)
    y = np.asarray(outcomes, dtype=float)
    return float(np.mean((p - y) ** 2))


def expected_calibration_error(probabilities: Sequence[float], outcomes: Sequence[bool],
                               n_bins: int = 10) -> float:
    """ECE with equal-width bins."""
    if not probabilities:
        return float("nan")
    p = np.clip(np.asarray(probabilities, dtype=float), 0.0, 1.0)
    y = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p > lo) & (p <= hi) if i else (p >= lo) & (p <= hi)
        if not mask.any():
            continue
        ece += (mask.sum() / len(p)) * abs(y[mask].mean() - p[mask].mean())
    return float(ece)


def calibration_report(probabilities: Sequence[float],
                       outcomes: Sequence[bool]) -> Dict[str, float]:
    return {
        "n": len(probabilities),
        "brier": round(brier_score(probabilities, outcomes), 5),
        "ece": round(expected_calibration_error(probabilities, outcomes), 5),
        "base_rate": round(float(np.mean(outcomes)), 5) if len(outcomes) else float("nan"),
    }


# ======================================================================
def frontier(metrics: Sequence[Any],
             safety: str = "rwh", utility: str = "bsr",
             privacy: str = "uer") -> List[Dict[str, Any]]:
    """Safety-utility-privacy frontier points, one per condition.

    Section 10.2 insists on plotting the frontier rather than selecting one
    favourable operating point after seeing the test set.
    """
    import statistics

    grouped: Dict[str, List[Any]] = {}
    for m in metrics:
        grouped.setdefault(m.condition, []).append(m)

    points = []
    for condition, items in sorted(grouped.items()):
        point = {
            "condition": condition,
            "safety": round(statistics.fmean(getattr(m, safety) for m in items), 4),
            "utility": round(statistics.fmean(getattr(m, utility) for m in items), 4),
            "privacy": round(statistics.fmean(getattr(m, privacy) for m in items), 4),
            "recall": round(statistics.fmean(m.descendant_recall for m in items), 4),
            "precision": round(statistics.fmean(m.descendant_precision for m in items), 4),
            "n": len(items),
        }
        points.append(point)

    # Pareto flag: not dominated on (low safety cost, high utility, low privacy cost).
    for p in points:
        p["pareto"] = not any(
            q is not p
            and q["safety"] <= p["safety"] and q["utility"] >= p["utility"]
            and q["privacy"] <= p["privacy"]
            and (q["safety"] < p["safety"] or q["utility"] > p["utility"]
                 or q["privacy"] < p["privacy"])
            for q in points
        )
    return points


def objective_j(metric, weights) -> float:
    """J = ls*RWH + lu*(1-BSR) + lp*UER + lr*DRR + lc*Cost   (Section 6.4)."""
    cost = min(1.0, metric.overhead.get("replays", 0) / 50.0)
    return round(
        weights.lambda_s * metric.rwh
        + weights.lambda_u * (1.0 - metric.bsr)
        + weights.lambda_p * metric.uer
        + weights.lambda_r * metric.drr
        + weights.lambda_c * cost,
        5,
    )


__all__ = [
    "paired_bootstrap", "BootstrapCI", "mcnemar_exact", "McNemarResult",
    "brier_score", "expected_calibration_error", "calibration_report",
    "frontier", "objective_j",
]
