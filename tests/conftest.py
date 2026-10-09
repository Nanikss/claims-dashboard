"""A tiny hand-built dataset whose every metric can be checked by hand.

Annual terms of 365 days, so premium earns at an exact daily rate:

  POL-1  CA  Commercial Property  2025-01-01 -> 2026-01-01  $3,650  (1,000 cents/day)
  POL-2  CA  General Liability    2025-01-01 -> 2026-01-01  $7,300  (2,000 cents/day)
  POL-3  NY  Commercial Property  2025-07-01 -> 2026-07-01  $3,650
  POL-4  TX  Commercial Auto      2025-01-01 -> 2026-01-01  $3,650  (no claims)

  claim  policy  loss        reported    lag  status  paid    reserve
  CLM-1  POL-1   2025-02-10  2025-02-15    5  closed  $1,000  $0
  CLM-2  POL-1   2025-02-20  2025-03-02   10  open    $500    $500
  CLM-3  POL-2   2025-04-05  2025-04-05    0  open    $0      $2,000
  CLM-4  POL-3   2025-08-15  2025-09-15   31  closed  $1,200  $0
"""
from __future__ import annotations

import pytest

from app import create_app
from app.db import load

POLICIES = [
    ("POL-1", "CA", "Commercial Property", "2025-01-01", "2026-01-01", 365_000),
    ("POL-2", "CA", "General Liability", "2025-01-01", "2026-01-01", 730_000),
    ("POL-3", "NY", "Commercial Property", "2025-07-01", "2026-07-01", 365_000),
    ("POL-4", "TX", "Commercial Auto", "2025-01-01", "2026-01-01", 365_000),
]
CLAIMS = [
    ("CLM-1", "POL-1", "2025-02-10", "2025-02-15", "closed", 100_000, 0),
    ("CLM-2", "POL-1", "2025-02-20", "2025-03-02", "open", 50_000, 50_000),
    ("CLM-3", "POL-2", "2025-04-05", "2025-04-05", "open", 0, 200_000),
    ("CLM-4", "POL-3", "2025-08-15", "2025-09-15", "closed", 120_000, 0),
]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    load(path, POLICIES, CLAIMS)
    return path


@pytest.fixture
def client(db_path):
    app = create_app({"TESTING": True, "DATABASE": str(db_path)})
    return app.test_client()
