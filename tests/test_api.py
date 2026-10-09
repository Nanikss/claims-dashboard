from __future__ import annotations

import sqlite3

import pytest

from app import create_app
from app.db import get_db, load
from app.sample_data import AS_OF, generate


# --- KPIs ---------------------------------------------------------------------

def test_kpis_match_hand_computed_values(client):
    assert client.get("/api/kpis").get_json() == {
        "claim_count": 4,
        "paid_cents": 270_000,
        "reserve_cents": 250_000,
        "incurred_cents": 520_000,
        "earned_premium_cents": 1_825_000,
        "loss_ratio": 0.2849,           # 520,000 / 1,825,000
        "avg_report_lag_days": 11.5,    # (5 + 10 + 0 + 31) / 4
        "open_claims": 2,
        "closed_claims": 2,
    }


def test_state_and_line_filters_combine(client):
    ca = client.get("/api/kpis?state=CA").get_json()
    assert (ca["claim_count"], ca["incurred_cents"], ca["earned_premium_cents"]) == (3, 400_000, 1_095_000)

    ca_property = client.get("/api/kpis", query_string={"state": "CA", "line": "Commercial Property"}).get_json()
    assert (ca_property["claim_count"], ca_property["incurred_cents"]) == (2, 200_000)
    assert ca_property["loss_ratio"] == 0.5479   # 200,000 / 365,000


def test_date_window_prorates_earned_premium(client):
    # Q1 2025 is 90 days: POL-1, POL-2 and POL-4 earn 90 days each; POL-3 isn't in force yet.
    body = client.get("/api/kpis?from=2025-01-01&to=2025-03-31").get_json()
    assert body["claim_count"] == 2
    assert body["incurred_cents"] == 200_000
    assert body["earned_premium_cents"] == 90 * (1_000 + 2_000 + 1_000)
    assert body["loss_ratio"] == 0.5556


def test_empty_result_returns_zeros_not_errors(client):
    resp = client.get("/api/kpis?from=2030-01-01")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["claim_count"] == body["incurred_cents"] == body["earned_premium_cents"] == 0
    assert body["open_claims"] == body["closed_claims"] == 0
    # Undefined rather than 0: nothing was earned and there is no lag to average.
    assert body["loss_ratio"] is None
    assert body["avg_report_lag_days"] is None

    assert client.get("/api/claims/monthly?from=2030-01-01").get_json() == {"months": []}
    assert client.get("/api/loss-ratio?from=2030-01-01").get_json() == {"by_state": [], "by_line": []}
    page = client.get("/api/claims?from=2030-01-01").get_json()
    assert (page["items"], page["total"], page["pages"]) == ([], 0, 0)


def test_loss_ratio_with_zero_premium_is_null_not_a_crash(tmp_path):
    path = tmp_path / "zero.db"
    load(path,
         [("POL-Z", "FL", "General Liability", "2025-01-01", "2026-01-01", 0)],
         [("CLM-Z", "POL-Z", "2025-03-01", "2025-03-02", "open", 0, 10_000)])
    client = create_app({"DATABASE": str(path)}).test_client()

    kpis = client.get("/api/kpis").get_json()
    assert (kpis["incurred_cents"], kpis["earned_premium_cents"], kpis["loss_ratio"]) == (10_000, 0, None)
    row = client.get("/api/loss-ratio").get_json()["by_state"][0]
    assert (row["state"], row["incurred_cents"], row["loss_ratio"]) == ("FL", 10_000, None)


# --- monthly and loss ratio -----------------------------------------------------

def test_monthly_series_fills_gaps_with_zero(client):
    months = client.get("/api/claims/monthly").get_json()["months"]
    assert [m["month"] for m in months] == ["2025-02", "2025-03", "2025-04", "2025-05",
                                            "2025-06", "2025-07", "2025-08"]
    assert months[0] == {"month": "2025-02", "claim_count": 2, "incurred_cents": 200_000}
    assert months[1] == {"month": "2025-03", "claim_count": 0, "incurred_cents": 0}
    assert sum(m["incurred_cents"] for m in months) == 520_000


def test_loss_ratio_by_state_and_line(client):
    body = client.get("/api/loss-ratio").get_json()
    by_state = {r["state"]: r for r in body["by_state"]}
    assert by_state["CA"]["loss_ratio"] == 0.3653   # 400,000 / 1,095,000
    assert by_state["NY"]["loss_ratio"] == 0.3288   # 120,000 / 365,000
    assert by_state["TX"] == {"state": "TX", "claim_count": 0, "earned_premium_cents": 365_000,
                              "incurred_cents": 0, "loss_ratio": 0.0}
    assert "FL" not in by_state                     # no policies, so no row

    by_line = {r["line"]: r["loss_ratio"] for r in body["by_line"]}
    assert by_line == {"Commercial Auto": 0.0, "Commercial Property": 0.4384, "General Liability": 0.274}


def test_loss_ratio_respects_filters(client):
    body = client.get("/api/loss-ratio?line=Commercial Property").get_json()
    assert [(r["state"], r["loss_ratio"]) for r in body["by_state"]] == [("CA", 0.5479), ("NY", 0.3288)]
    assert [r["line"] for r in body["by_line"]] == ["Commercial Property"]


# --- validation -----------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "from=2025-13-01", "to=2025-02-30", "from=yesterday", "from=20250101", "to=2025-W01-1",
])
def test_bad_dates_are_rejected_on_every_endpoint(client, query):
    for endpoint in ("/api/kpis", "/api/claims/monthly", "/api/loss-ratio", "/api/claims"):
        resp = client.get(f"{endpoint}?{query}")
        assert resp.status_code == 400
        assert "ISO date" in resp.get_json()["error"]


def test_from_after_to_is_rejected(client):
    resp = client.get("/api/kpis?from=2025-06-01&to=2025-05-31")
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "'from' must be on or before 'to'"}


@pytest.mark.parametrize("params", [
    {"state": "ZZ"}, {"state": "ca"}, {"state": "CA' OR '1'='1"}, {"line": "Marine"},
])
def test_unknown_state_or_line_is_rejected(client, params):
    resp = client.get("/api/kpis", query_string=params)
    assert resp.status_code == 400
    assert resp.get_json()["error"].startswith("unknown")


# --- claim list: sorting and paging ---------------------------------------------

@pytest.mark.parametrize("sort", [
    "loss_date; DROP TABLE claims", "loss_date DESC, (SELECT 1)", "c.paid_cents", "1",
])
def test_sql_injection_in_sort_is_rejected(client, db_path, sort):
    resp = client.get("/api/claims", query_string={"sort": sort})
    assert resp.status_code == 400
    assert "unknown sort" in resp.get_json()["error"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 4


def test_sort_and_order(client):
    def ids(query):
        return [c["claim_id"] for c in client.get(f"/api/claims?{query}").get_json()["items"]]

    # CLM-1 and CLM-2 tie at $1,000 incurred; ties break on claim_id so paging is stable.
    assert ids("sort=incurred_cents&order=desc") == ["CLM-3", "CLM-4", "CLM-1", "CLM-2"]
    assert ids("sort=report_lag_days&order=asc") == ["CLM-3", "CLM-1", "CLM-2", "CLM-4"]
    assert ids("") == ["CLM-4", "CLM-3", "CLM-2", "CLM-1"]  # default: most recent loss first
    assert client.get("/api/claims?order=sideways").status_code == 400


def test_pagination_bounds(client):
    first = client.get("/api/claims?per_page=3&page=1&sort=claim_id&order=asc").get_json()
    assert [c["claim_id"] for c in first["items"]] == ["CLM-1", "CLM-2", "CLM-3"]
    assert (first["page"], first["per_page"], first["total"], first["pages"]) == (1, 3, 4, 2)

    last = client.get("/api/claims?per_page=3&page=2&sort=claim_id&order=asc").get_json()
    assert [c["claim_id"] for c in last["items"]] == ["CLM-4"]

    past_end = client.get("/api/claims?per_page=3&page=3").get_json()
    assert (past_end["items"], past_end["total"]) == ([], 4)


@pytest.mark.parametrize("query", [
    "page=0", "page=-1", "page=abc", "per_page=0", "per_page=101", "per_page=1e3", "per_page=1_0",
])
def test_invalid_paging_is_rejected(client, query):
    assert client.get(f"/api/claims?{query}").status_code == 400


def test_claim_rows_carry_derived_fields(client):
    item = client.get("/api/claims?sort=claim_id&order=asc&per_page=1").get_json()["items"][0]
    assert item == {"claim_id": "CLM-1", "policy_id": "POL-1", "state": "CA",
                    "line": "Commercial Property", "loss_date": "2025-02-10",
                    "reported_date": "2025-02-15", "report_lag_days": 5, "status": "closed",
                    "paid_cents": 100_000, "reserve_cents": 0, "incurred_cents": 100_000}


# --- app, frontend, sample data -------------------------------------------------

def test_index_page_renders_filter_options(client):
    html = client.get("/").get_data(as_text=True)
    assert '<option value="FL">FL</option>' in html
    assert '<option value="General Liability">General Liability</option>' in html


def test_api_connection_is_read_only(client):
    with client.application.app_context():
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            get_db().execute("DELETE FROM claims")


def test_sample_data_is_deterministic_and_consistent(tmp_path):
    policies, claims = generate(seed=7)
    assert (policies, claims) == generate(seed=7)
    assert len(claims) == 2000

    path = tmp_path / "sample.db"
    load(path, policies, claims)  # schema CHECK constraints and the foreign key hold
    with sqlite3.connect(path) as conn:
        out_of_term, after_cutoff, closed_with_reserve = conn.execute(
            """SELECT SUM(c.loss_date < p.effective_date OR c.loss_date >= p.expiration_date),
                      SUM(c.reported_date > ?),
                      SUM(c.status = 'closed' AND c.reserve_cents > 0)
               FROM claims c JOIN policies p ON p.policy_id = c.policy_id""",
            (AS_OF.isoformat(),)).fetchone()
    assert out_of_term == after_cutoff == closed_with_reserve == 0
