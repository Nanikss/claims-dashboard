"""Deterministic sample policies and claims for the dashboard.

Same domain as the claims pipeline's staging tables (policies + claims, money in
cents), already clean, since this is the reporting layer. The shapes are chosen
so the charts have something to show: liability claims are reported later than
property or auto, Florida property is hit harder, and older claims are more
likely to be closed.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from .db import ClaimRow, PolicyRow
from .filters import LINES, STATES

AS_OF = date(2026, 9, 30)  # data cut-off; every policy term below ends before it
FIRST_EFFECTIVE = date(2024, 7, 1)
LAST_EFFECTIVE = date(2025, 9, 30)

# line -> (premium range in dollars, mean claim severity in dollars, mean reporting lag in days)
LINE_PROFILE = {
    "Commercial Property": ((12_000, 60_000), 9_000, 8),
    "General Liability": ((6_000, 35_000), 6_000, 35),
    "Commercial Auto": ((8_000, 45_000), 6_000, 5),
}
STATE_SEVERITY = {"CA": 1.10, "NY": 1.15, "TX": 0.85, "FL": 1.05}
FL_PROPERTY_SEVERITY = 1.6  # wind/water losses on top of the state factor


def _cents(dollars: float) -> int:
    return int(round(dollars * 100))


def _severity(rng: random.Random, mean: float, sigma: float = 1.0) -> float:
    # Lognormal with the given mean; heavy right tail like real claim severity.
    mu = math.log(mean) - sigma * sigma / 2
    return min(rng.lognormvariate(mu, sigma), 40 * mean)


def generate(seed: int = 42, policies: int = 800, claims: int = 2000) -> tuple[list[PolicyRow], list[ClaimRow]]:
    rng = random.Random(seed)
    span = (LAST_EFFECTIVE - FIRST_EFFECTIVE).days

    policy_rows: list[PolicyRow] = []
    for i in range(1, policies + 1):
        state, line = rng.choice(STATES), rng.choice(LINES)
        effective = FIRST_EFFECTIVE + timedelta(days=rng.randrange(span + 1))
        expiration = effective.replace(year=effective.year + 1)  # range contains no Feb 29
        lo, hi = LINE_PROFILE[line][0]
        policy_rows.append((f"POL-{i:05d}", state, line, effective.isoformat(),
                            expiration.isoformat(), _cents(rng.uniform(lo, hi))))

    claim_rows: list[ClaimRow] = []
    while len(claim_rows) < claims:
        policy_id, state, line, eff, exp, _ = rng.choice(policy_rows)
        _, mean_severity, mean_lag = LINE_PROFILE[line]
        lag = min(int(rng.expovariate(1 / mean_lag)), 365)
        effective, expiration = date.fromisoformat(eff), date.fromisoformat(exp)
        last_loss = min(expiration - timedelta(days=1), AS_OF - timedelta(days=lag))
        if last_loss < effective:
            continue  # this lag can't fit inside the term before the cut-off; draw again
        loss = effective + timedelta(days=rng.randrange((last_loss - effective).days + 1))
        reported = loss + timedelta(days=lag)

        factor = STATE_SEVERITY[state] * (FL_PROPERTY_SEVERITY if (state, line) == ("FL", "Commercial Property") else 1)
        ultimate = _severity(rng, mean_severity * factor)
        age_days = (AS_OF - reported).days
        if rng.random() < min(0.9, age_days / 400):
            status = "closed"
            paid, reserve = (0.0 if rng.random() < 0.1 else ultimate), 0.0  # some close without payment
        else:
            status = "open"
            paid = ultimate * rng.uniform(0, 0.6)
            reserve = ultimate - paid
        claim_rows.append((f"CLM-{len(claim_rows) + 1:06d}", policy_id, loss.isoformat(),
                           reported.isoformat(), status, _cents(paid), _cents(reserve)))

    return policy_rows, claim_rows
