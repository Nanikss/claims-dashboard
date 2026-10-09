"""Reporting queries. All aggregation happens in SQL; Python only shapes the JSON.

Every query is a constant string with named parameters. Filters are written as
``(:x IS NULL OR col = :x)`` so the SQL text never changes with user input; the
only dynamic part is ORDER BY, which is chosen from a whitelist.

Loss ratio is incurred losses (paid + case reserve) over *earned* premium. With
no date filter that is the full written premium. With a date window, losses are
the claims whose loss date falls in the window and premium is each policy's
written premium pro-rated by the days of its term inside the window, so a
one-quarter window isn't compared against a full year of premium.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .filters import Filters

POLICY_FILTER = """
        (:state IS NULL OR p.state = :state)
    AND (:line  IS NULL OR p.line  = :line)
"""

CLAIM_FILTER = POLICY_FILTER + """
    AND (:date_from IS NULL OR c.loss_date >= :date_from)
    AND (:date_to   IS NULL OR c.loss_date <= :date_to)
"""

# Per-policy earned premium inside [date_from, date_to]. expiration_date is
# exclusive, hence date_to + 1 day. The schema guarantees a non-zero term.
EARNED_CTE = f"""
earned AS (
    SELECT p.state,
           p.line,
           p.written_premium_cents
             * MAX(0, julianday(MIN(p.expiration_date,
                                    COALESCE(date(:date_to, '+1 day'), p.expiration_date)))
                    - julianday(MAX(p.effective_date, COALESCE(:date_from, p.effective_date))))
             / (julianday(p.expiration_date) - julianday(p.effective_date)) AS earned_cents
    FROM policies p
    WHERE {POLICY_FILTER}
)
"""

# CASE ... END yields NULL rather than raising or returning 0 when there is no
# earned premium: a 0% loss ratio would read as a very good result.
KPI_SQL = f"""
WITH {EARNED_CTE},
claim_totals AS (
    SELECT COUNT(*)                                  AS claim_count,
           COALESCE(SUM(c.paid_cents), 0)            AS paid_cents,
           COALESCE(SUM(c.reserve_cents), 0)         AS reserve_cents,
           COALESCE(SUM(c.status = 'open'), 0)       AS open_claims,
           COALESCE(SUM(c.status = 'closed'), 0)     AS closed_claims,
           ROUND(AVG(julianday(c.reported_date) - julianday(c.loss_date)), 1) AS avg_report_lag_days
    FROM claims c
    JOIN policies p ON p.policy_id = c.policy_id
    WHERE {CLAIM_FILTER}
),
premium AS (
    SELECT CAST(ROUND(COALESCE(SUM(earned_cents), 0)) AS INTEGER) AS earned_premium_cents
    FROM earned
)
SELECT ct.claim_count,
       ct.paid_cents,
       ct.reserve_cents,
       ct.paid_cents + ct.reserve_cents AS incurred_cents,
       pr.earned_premium_cents,
       CASE WHEN pr.earned_premium_cents > 0
            THEN ROUND(1.0 * (ct.paid_cents + ct.reserve_cents) / pr.earned_premium_cents, 4)
       END AS loss_ratio,
       ct.avg_report_lag_days,
       ct.open_claims,
       ct.closed_claims
FROM claim_totals ct CROSS JOIN premium pr
"""

# Months with no claims are filled with zeros so the line chart doesn't
# silently join across a gap.
MONTHLY_SQL = f"""
WITH RECURSIVE by_month AS (
    SELECT substr(c.loss_date, 1, 7)                AS month,
           COUNT(*)                                 AS claim_count,
           SUM(c.paid_cents + c.reserve_cents)      AS incurred_cents
    FROM claims c
    JOIN policies p ON p.policy_id = c.policy_id
    WHERE {CLAIM_FILTER}
    GROUP BY month
),
bounds AS (SELECT MIN(month) AS lo, MAX(month) AS hi FROM by_month),
months(month) AS (
    SELECT lo FROM bounds WHERE lo IS NOT NULL
    UNION ALL
    SELECT strftime('%Y-%m', months.month || '-01', '+1 month')
    FROM months, bounds
    WHERE months.month < bounds.hi
)
SELECT m.month,
       COALESCE(b.claim_count, 0)    AS claim_count,
       COALESCE(b.incurred_cents, 0) AS incurred_cents
FROM months m
LEFT JOIN by_month b ON b.month = m.month
ORDER BY m.month
"""


def _loss_ratio_sql(dimension: str) -> str:
    # Called only at import time with the two literals below, never with input.
    assert dimension in ("state", "line")
    return f"""
WITH {EARNED_CTE},
premium AS (
    SELECT {dimension} AS grp, SUM(earned_cents) AS earned_cents
    FROM earned
    GROUP BY grp
),
losses AS (
    SELECT p.{dimension} AS grp,
           COUNT(*) AS claim_count,
           SUM(c.paid_cents + c.reserve_cents) AS incurred_cents
    FROM claims c
    JOIN policies p ON p.policy_id = c.policy_id
    WHERE {CLAIM_FILTER}
    GROUP BY grp
),
joined AS (
    SELECT pr.grp,
           COALESCE(l.claim_count, 0)                     AS claim_count,
           CAST(ROUND(pr.earned_cents) AS INTEGER)        AS earned_premium_cents,
           COALESCE(l.incurred_cents, 0)                  AS incurred_cents
    FROM premium pr
    LEFT JOIN losses l ON l.grp = pr.grp
)
SELECT grp AS {dimension},
       claim_count,
       earned_premium_cents,
       incurred_cents,
       CASE WHEN earned_premium_cents > 0
            THEN ROUND(1.0 * incurred_cents / earned_premium_cents, 4)
       END AS loss_ratio
FROM joined
WHERE earned_premium_cents > 0 OR claim_count > 0
ORDER BY grp
"""


LOSS_RATIO_SQL = {dim: _loss_ratio_sql(dim) for dim in ("state", "line")}

# Public sort key -> SQL expression. Anything not in here is rejected with a 400.
SORT_COLUMNS = {
    "claim_id": "c.claim_id",
    "state": "p.state",
    "line": "p.line",
    "loss_date": "c.loss_date",
    "reported_date": "c.reported_date",
    "report_lag_days": "report_lag_days",
    "status": "c.status",
    "paid_cents": "c.paid_cents",
    "reserve_cents": "c.reserve_cents",
    "incurred_cents": "incurred_cents",
}
SORT_ORDERS = {"asc": "ASC", "desc": "DESC"}

CLAIMS_SELECT = f"""
SELECT c.claim_id,
       c.policy_id,
       p.state,
       p.line,
       c.loss_date,
       c.reported_date,
       CAST(julianday(c.reported_date) - julianday(c.loss_date) AS INTEGER) AS report_lag_days,
       c.status,
       c.paid_cents,
       c.reserve_cents,
       c.paid_cents + c.reserve_cents AS incurred_cents
FROM claims c
JOIN policies p ON p.policy_id = c.policy_id
WHERE {CLAIM_FILTER}
"""

CLAIMS_COUNT_SQL = f"""
SELECT COUNT(*)
FROM claims c
JOIN policies p ON p.policy_id = c.policy_id
WHERE {CLAIM_FILTER}
"""


def kpis(db: sqlite3.Connection, filters: Filters) -> dict[str, Any]:
    return dict(db.execute(KPI_SQL, filters.params()).fetchone())


def monthly(db: sqlite3.Connection, filters: Filters) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(MONTHLY_SQL, filters.params())]


def loss_ratio(db: sqlite3.Connection, filters: Filters) -> dict[str, list[dict[str, Any]]]:
    return {
        f"by_{dim}": [dict(r) for r in db.execute(sql, filters.params())]
        for dim, sql in LOSS_RATIO_SQL.items()
    }


def claims_page(db: sqlite3.Connection, filters: Filters, *, sort: str, order: str,
                page: int, per_page: int) -> dict[str, Any]:
    """``sort`` and ``order`` must already be validated against the whitelists."""
    order_by = f"ORDER BY {SORT_COLUMNS[sort]} {SORT_ORDERS[order]}, c.claim_id ASC"
    params = {**filters.params(), "limit": per_page, "offset": (page - 1) * per_page}
    total = db.execute(CLAIMS_COUNT_SQL, params).fetchone()[0]
    rows = db.execute(f"{CLAIMS_SELECT} {order_by} LIMIT :limit OFFSET :offset", params)
    return {
        "items": [dict(r) for r in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page,
    }
