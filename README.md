# Claims Dashboard

[![CI](https://github.com/kumarnenavath5815-cpu/claims-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/kumarnenavath5815-cpu/claims-dashboard/actions/workflows/ci.yml)

A small reporting layer for insurance claims data: a **Flask JSON API over SQLite** and a single-page dashboard with KPI tiles, a monthly trend, loss ratio by state or line of business, and a sortable, paged claims table. It sits on top of the same kind of data my [claims pipeline](https://github.com/kumarnenavath5815-cpu/claims-pipeline) publishes (policies and claims for CA/NY/TX/FL across three commercial lines), but it is self-contained and ships its own deterministic sample data (800 policies, 2,000 claims).

**Stack:** Python 3.10+ · Flask · SQL (SQLite) · Chart.js · pytest.

## What it does

1. **KPIs:** claim count, incurred losses (paid + case reserve), earned premium, loss ratio, average reporting lag (loss date to report date) and open vs closed claims.
2. **Monthly trend:** claim count and incurred by loss month. Months with no claims are filled with zeros so the chart doesn't draw a line straight across a gap.
3. **Loss ratio by state and by line of business.**
4. **Claim list:** paginated, sortable on any column, with a stable tie-break so paging never repeats or skips a row.
5. **Filters on everything:** `state`, `line`, `from` and `to` (loss date, inclusive). Every endpoint applies the same filters, so the tiles, charts and table always describe the same set of claims.

## Design decisions

- **Parameterised SQL, plus a whitelist for ORDER BY.** Every query is a constant string with named parameters. Optional filters are written as `(:state IS NULL OR p.state = :state)`, so the SQL text never changes with the request. ORDER BY can't be parameterised, so the sort key is looked up in a dict of allowed columns; anything else (`loss_date; DROP TABLE claims`) gets a 400 before it reaches the database. Inputs are validated too: unknown state or line, a malformed date or `from` after `to` all return a 400 with a JSON `error`. As a second line of defence, the API opens SQLite read-only.
- **Money as integer cents.** It's stored, summed and returned as integers, so totals are exact and reconcile to the cent. The frontend divides by 100 only to display it.
- **Aggregation in SQL, not Python.** Sums, averages, the loss ratio and the month series are all computed in the query, and Python only shapes the JSON. This keeps response size independent of row count, and the same SQL can move to a warehouse largely unchanged.
- **Loss ratio on earned premium, and divide-by-zero safe.** With a date window, losses are claims whose loss date falls in the window, and premium is each policy's written premium pro-rated by the days of its term inside that window. Comparing one quarter of losses with a full year of written premium would understate the ratio by about 4x. With no premium earned the ratio is `null` (`CASE WHEN premium > 0 ...`), not an error and not `0`, because 0% would read as a very good result. Empty filters return zero counts and amounts with a 200.
- **App factory.** `create_app(config)` builds the app, so tests can point it at a temporary database and the CLI and dev server share the same setup.

## API

All endpoints accept `state` (CA, NY, TX, FL), `line` (Commercial Property, General Liability, Commercial Auto), `from` and `to` (YYYY-MM-DD). Money fields are integer cents and `loss_ratio` is a fraction (0.6583 = 65.83%).

| Endpoint | Returns |
|---|---|
| `GET /api/kpis` | `claim_count`, `paid_cents`, `reserve_cents`, `incurred_cents`, `earned_premium_cents`, `loss_ratio`, `avg_report_lag_days`, `open_claims`, `closed_claims` |
| `GET /api/claims/monthly` | `months: [{month, claim_count, incurred_cents}]`, zero-filled |
| `GET /api/loss-ratio` | `by_state` and `by_line`: `[{state or line, claim_count, earned_premium_cents, incurred_cents, loss_ratio}]` |
| `GET /api/claims` | `items`, `page`, `per_page`, `total`, `pages`. Extra params: `sort` (any field in `items`, e.g. `incurred_cents`), `order` (`asc`/`desc`), `page` (≥ 1), `per_page` (1–100) |

Bad input → `400 {"error": "..."}`.

## Run

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
flask --app app seed                    # (re)build instance/claims.db; --seed and --claims are optional
flask --app app run                     # http://127.0.0.1:5000
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q                               # 35 tests (19 test functions, some parametrised)
```

The tests run against a temporary SQLite database seeded with four policies and four claims, small enough to work every expected number out by hand (the arithmetic is in `tests/conftest.py`). They cover exact KPI values, filters, earned-premium pro-rating, gap-filled months, date and input validation, SQL injection through the sort parameter, pagination bounds, empty results and the zero-premium case, the read-only connection, and the consistency of the sample-data generator. CI runs them on every push (GitHub Actions, Python 3.12).

## Layout

```
app/__init__.py      create_app() factory, index route
app/api.py           JSON endpoints: validate -> query -> jsonify
app/filters.py       query-string parsing and validation (whitelists, strict ISO dates)
app/queries.py       all SQL: KPIs, monthly series, loss ratio, paged claim list
app/db.py            schema (cents, CHECK constraints, indexes), loader, read-only connection
app/sample_data.py   deterministic sample data generator
app/cli.py           `flask seed` command
app/templates/       single page
app/static/          app.js (fetch + Chart.js), style.css (responsive, light/dark)
tests/               pytest suite with a hand-checked fixture dataset
```

## Interview notes

- **Trade-off: written vs earned vs ultimate.** I pro-rate written premium to get earned premium, which is right for a date window, but incurred losses here are paid plus case reserves only. A real loss ratio report would add IBNR (incurred but not reported), which is why the most recent months look better than they will turn out to be. I'd take IBNR factors from the actuarial team and keep them out of this layer.
- **Caching.** The data only changes when the pipeline publishes, so the cache key can be the filter set plus a batch id, and responses can be cached until the next load: an in-process LRU or Redis, plus an `ETag` so the browser can revalidate cheaply. Cache invalidation is then one event, not a TTL guess.
- **Moving to Postgres or Redshift.** The SQL is mostly portable: `julianday` becomes date subtraction and the recursive month CTE becomes `generate_series`. On Redshift I'd read the pipeline's pre-aggregated marts for the tiles and charts, and keep the claim-level list on a sort key of `loss_date` so the date filter prunes blocks. The `(:x IS NULL OR ...)` pattern can stop Postgres from using an index, so at scale I'd switch to a query builder that only adds the clauses it needs.
- **Auth.** There's none, since this is a demo. In production it would sit behind SSO (OIDC) with a role check per endpoint. Claims data is sensitive, so I'd also add row-level scoping (for example, by state or business unit) enforced in the query, not the UI.
- **Why not pandas?** It isn't needed for this. Every metric is a GROUP BY, and keeping the logic in SQL means it's tested once and runs the same wherever the data lives.

Built with AI coding tools; I designed the metrics and data model from my experience with insurance data, and reviewed and tested the code.
