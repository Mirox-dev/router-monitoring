from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True, slots=True)
class Profile:
    median: float
    p95: float
    mad: float
    samples: int


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def build_profile(values: list[float]) -> Profile | None:
    if not values:
        return None
    center = median(values)
    deviations = [abs(value - center) for value in values]
    return Profile(center, _percentile(values, 0.95), median(deviations), len(values))


def is_anomalous(value: float, profile: Profile, minimum_delta: float = 0.0) -> bool:
    tolerance = max(profile.mad * 6, minimum_delta)
    return abs(value - profile.median) > tolerance
