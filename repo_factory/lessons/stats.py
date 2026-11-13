"""Statistics for comparing arms (build v5 §2.6, §2.7). Pure functions, no I/O.

The paired unit is one base build, a (pack, seed). Rows within a build are never treated as
independent samples. Inference is conditional on the packs; generalisation is argued only
from held-out packs. Moved from ``hsuite/`` (S-5b): ``/history`` reads them."""

from __future__ import annotations

import itertools
import math
import random
from statistics import mean, stdev

EXACT_MAX_N = 20  # 2^20 sign patterns; beyond that a seeded Monte Carlo estimate
MC_SAMPLES = 200_000


def sign_flip_p(diffs: list[float], *, alternative: str = "less", seed: int = 0) -> float:
    """One-sided paired sign-flip permutation test on per-unit differences
    ``d = challenger - baseline`` (core failures, so lower is better).

    ``alternative="less"``: H1 is mean(d) < 0. The p-value is the share of the 2^n sign
    assignments whose mean is <= the observed mean (ties count, so p >= 1/2^n). Exact up to
    EXACT_MAX_N units. Zero differences contribute nothing to any assignment."""
    d = [float(x) for x in diffs]
    if not d:
        return 1.0
    if alternative not in ("less", "greater"):
        raise ValueError("alternative must be 'less' or 'greater'")
    sgn = 1.0 if alternative == "less" else -1.0
    obs = sgn * sum(d)
    eps = 1e-9 * max(1.0, sum(abs(x) for x in d))
    n = len(d)
    if n <= EXACT_MAX_N:
        hits = 0
        for signs in itertools.product((1.0, -1.0), repeat=n):
            if sgn * sum(s * x for s, x in zip(signs, d)) <= obs + eps:
                hits += 1
        return hits / 2**n
    rng = random.Random(seed)
    hits = sum(
        sgn * sum(x if rng.random() < 0.5 else -x for x in d) <= obs + eps
        for _ in range(MC_SAMPLES)
    )
    return (hits + 1) / (MC_SAMPLES + 1)


def mean_ratio(a: list[float], b: list[float]) -> float:
    """mean(a) / mean(b): e.g. the challenger's mean core failures over the baseline's.
    0/0 is 1.0 (both perfect); x/0 with x > 0 is inf."""
    ma, mb = mean(a), mean(b)
    if mb == 0:
        return 1.0 if ma == 0 else math.inf
    return ma / mb


def packs_better(a_by_pack: dict[str, list[float]], b_by_pack: dict[str, list[float]]) -> int:
    """Packs where the mean of ``a`` is strictly lower (fewer failures) than that of ``b``."""
    return sum(
        1
        for p in a_by_pack
        if p in b_by_pack
        and a_by_pack[p]
        and b_by_pack[p]
        and mean(a_by_pack[p]) < mean(b_by_pack[p])
    )


def packs_worse_by(
    a_by_pack: dict[str, list[float]], b_by_pack: dict[str, list[float]], margin: float
) -> list[str]:
    """Packs where ``a`` is worse than ``b`` by more than ``margin`` (e.g. sigma_post)."""
    return [
        p
        for p in a_by_pack
        if p in b_by_pack
        and a_by_pack[p]
        and b_by_pack[p]
        and mean(a_by_pack[p]) - mean(b_by_pack[p]) > margin
    ]


def cost_ratios(
    arm_usd: dict[str, list[float]], vanilla_usd: dict[str, list[float]]
) -> dict[str, float]:
    """Per pack: mean(arm cost) / mean(vanilla cost on that pack, same profile) (the 3x rule)."""
    out = {}
    for p, xs in arm_usd.items():
        v = vanilla_usd.get(p) or []
        if xs and v and mean(v) > 0:
            out[p] = mean(xs) / mean(v)
    return out


def cost_rule(ratios: dict[str, float], mean_max: float = 3.0, pack_max: float = 3.5) -> dict:
    """Pass when the mean ratio over packs is <= mean_max and no pack exceeds pack_max."""
    if not ratios:
        return {"mean": None, "max": None, "pass": False}
    m, x = mean(ratios.values()), max(ratios.values())
    return {"mean": m, "max": x, "pass": m <= mean_max and x <= pack_max}


def sigma(xs: list[float]) -> float | None:
    """Sample standard deviation (n-1); None below two values."""
    return stdev(xs) if len(xs) >= 2 else None


def unpaired_threshold(sigma_build: float) -> float:
    """A difference between two single unpaired runs must exceed 2.8 x sigma_build (two-sided
    95% for the difference of two draws) before it counts (§2.7)."""
    return 2.8 * sigma_build


def adapter_spread(pairs: list[tuple[float, float]]) -> float | None:
    """sigma_adapter from two independent adapters on the same builds: the largest absolute
    difference in core failures (the gate is <= 1 row)."""
    return max(abs(a - b) for a, b in pairs) if pairs else None
