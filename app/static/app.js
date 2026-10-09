"use strict";

(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const view = {
    sort: "loss_date",
    order: "desc",
    page: 1,
    perPage: 25,
    monthlyMeasure: "incurred_cents",
    lrGroup: "state",
  };
  let monthlyData = [];
  let lossRatioData = { by_state: [], by_line: [] };

  // Formatting. The API sends money as integer cents; dividing only for display.
  const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  const usdCompact = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
  const count = new Intl.NumberFormat("en-US");
  const money = (cents) => usd.format(cents / 100);
  const pct = (ratio) => (ratio === null ? "n/a" : `${(ratio * 100).toFixed(1)}%`);
  const monthLabel = (ym) => {
    const [y, m] = ym.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString("en-US", { month: "short", year: "numeric" });
  };

  // --- API ---------------------------------------------------------------------

  function filterParams() {
    const params = {};
    for (const [key, value] of new FormData($("#filters"))) {
      if (value) params[key] = value;
    }
    return params;
  }

  async function getJSON(path, params) {
    const resp = await fetch(`${path}?${new URLSearchParams(params)}`);
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.error || `Request failed (${resp.status})`);
    return body;
  }

  // If the Chart.js CDN is unreachable the figures and tables still work.
  const chartsAvailable = typeof Chart !== "undefined";
  const idleStatus = chartsAvailable ? "" : "Charts could not be loaded; the data tables under each chart still work.";

  function setStatus(message, isError = false) {
    const el = $("#status");
    el.textContent = message || idleStatus;
    el.classList.toggle("error", isError);
  }

  // A newer request supersedes an older one, so a slow response can't
  // overwrite the result of a later filter change.
  let summaryRequest = 0;
  let tableRequest = 0;

  async function loadSummary() {
    const id = ++summaryRequest;
    const params = filterParams();
    try {
      const [kpis, monthly, lossRatio] = await Promise.all([
        getJSON("/api/kpis", params),
        getJSON("/api/claims/monthly", params),
        getJSON("/api/loss-ratio", params),
      ]);
      if (id !== summaryRequest) return;
      setStatus("");
      renderKpis(kpis);
      monthlyData = monthly.months;
      lossRatioData = lossRatio;
      renderMonthly();
      renderLossRatio();
    } catch (err) {
      if (id === summaryRequest) setStatus(err.message, true);
    }
  }

  async function loadTable() {
    const id = ++tableRequest;
    const params = { ...filterParams(), sort: view.sort, order: view.order, page: view.page, per_page: view.perPage };
    try {
      const data = await getJSON("/api/claims", params);
      if (id !== tableRequest) return;
      renderTable(data);
    } catch (err) {
      if (id === tableRequest) setStatus(err.message, true);
    }
  }

  function reloadAll() {
    view.page = 1;
    loadSummary();
    loadTable();
  }

  // --- KPIs --------------------------------------------------------------------

  function renderKpis(k) {
    const values = {
      claim_count: count.format(k.claim_count),
      incurred_cents: money(k.incurred_cents),
      earned_premium_cents: money(k.earned_premium_cents),
      loss_ratio: pct(k.loss_ratio),
      avg_report_lag_days: k.avg_report_lag_days === null ? "n/a" : `${k.avg_report_lag_days} days`,
      open_closed: `${count.format(k.open_claims)} / ${count.format(k.closed_claims)}`,
    };
    for (const el of $$("[data-kpi]")) el.textContent = values[el.dataset.kpi];
  }

  // --- charts ------------------------------------------------------------------

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function baseOptions() {
    const grid = css("--grid");
    const muted = css("--text-muted");
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false }, // one series per chart; the heading names it
        tooltip: { backgroundColor: css("--tooltip-bg"), titleColor: css("--tooltip-text"), bodyColor: css("--tooltip-text"), padding: 10 },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: muted }, border: { color: grid } },
        y: { beginAtZero: true, grid: { color: grid }, ticks: { color: muted }, border: { display: false } },
      },
    };
  }

  let monthlyChart;
  let lrChart;

  function renderMonthly() {
    const measure = view.monthlyMeasure;
    const isMoney = measure === "incurred_cents";
    const values = monthlyData.map((m) => (isMoney ? m[measure] / 100 : m[measure]));
    const options = baseOptions();
    options.interaction = { mode: "index", intersect: false };
    options.scales.y.ticks.callback = (v) => (isMoney ? usdCompact.format(v) : count.format(v));
    options.scales.x.ticks.maxRotation = 0;
    options.scales.x.ticks.autoSkipPadding = 12;
    options.plugins.tooltip.callbacks = {
      label: (ctx) => (isMoney ? ` Incurred ${usd.format(ctx.parsed.y)}` : ` ${count.format(ctx.parsed.y)} claims`),
    };
    const dataset = {
      data: values,
      borderColor: css("--series-1"),
      backgroundColor: css("--series-1"),
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 5,
      pointHitRadius: 12,
      cubicInterpolationMode: "monotone",
    };
    const labels = monthlyData.map((m) => monthLabel(m.month));
    if (monthlyChart) monthlyChart.destroy();
    if (chartsAvailable) monthlyChart = new Chart($("#monthly-chart"), { type: "line", data: { labels, datasets: [dataset] }, options });

    const what = isMoney ? "incurred losses" : "claim count";
    $("#monthly-chart").setAttribute("aria-label", `Line chart of ${what} by loss month, ${labels.length} months. Data table below.`);
    fillRows($("#monthly-table"), monthlyData.map((m) => [monthLabel(m.month), count.format(m.claim_count), money(m.incurred_cents)]));
  }

  function renderLossRatio() {
    const group = view.lrGroup;
    const rows = lossRatioData[`by_${group}`];
    const options = baseOptions();
    options.scales.y.ticks.callback = (v) => `${Math.round(v * 100)}%`;
    options.plugins.tooltip.callbacks = {
      label: (ctx) => {
        const r = rows[ctx.dataIndex];
        return [` Loss ratio ${pct(r.loss_ratio)}`, ` Incurred ${money(r.incurred_cents)}`, ` Earned ${money(r.earned_premium_cents)}`];
      },
    };
    const dataset = {
      data: rows.map((r) => r.loss_ratio),
      backgroundColor: css("--series-1"),
      hoverBackgroundColor: css("--series-1-hover"),
      borderRadius: { topLeft: 4, topRight: 4 },
      borderSkipped: "bottom",
      maxBarThickness: 48,
    };
    if (lrChart) lrChart.destroy();
    if (chartsAvailable) lrChart = new Chart($("#lr-chart"), { type: "bar", data: { labels: rows.map((r) => r[group]), datasets: [dataset] }, options });

    const label = group === "state" ? "state" : "line of business";
    $("#lr-chart").setAttribute("aria-label", `Bar chart of loss ratio by ${label}. Data table below.`);
    $("#lr-group-col").textContent = group === "state" ? "State" : "Line";
    fillRows($("#lr-table"), rows.map((r) => [r[group], money(r.earned_premium_cents), money(r.incurred_cents), pct(r.loss_ratio)]));
  }

  // --- tables ------------------------------------------------------------------

  // textContent only: nothing from the API is ever parsed as HTML.
  function fillRows(tbody, rows, numericFrom = 1, emptyText = "No data for these filters") {
    tbody.replaceChildren();
    if (rows.length === 0) {
      const tr = tbody.insertRow();
      const td = tr.insertCell();
      td.colSpan = tbody.closest("table").tHead.rows[0].cells.length;
      td.className = "empty";
      td.textContent = emptyText;
      return;
    }
    for (const row of rows) {
      const tr = document.createElement("tr");
      row.forEach((value, i) => {
        const td = document.createElement("td");
        td.textContent = value;
        if (Array.isArray(numericFrom) ? numericFrom.includes(i) : i >= numericFrom) td.className = "num";
        tr.append(td);
      });
      tbody.append(tr);
    }
  }

  function renderTable(data) {
    const rows = data.items.map((c) => [
      c.claim_id, c.state, c.line, c.loss_date, c.reported_date, String(c.report_lag_days),
      c.status, money(c.paid_cents), money(c.reserve_cents), money(c.incurred_cents),
    ]);
    fillRows($("#claims-body"), rows, [5, 7, 8, 9], "No claims match these filters");

    for (const th of $$("th[data-sort]")) {
      const active = th.dataset.sort === view.sort;
      if (active) th.setAttribute("aria-sort", view.order === "asc" ? "ascending" : "descending");
      else th.removeAttribute("aria-sort");
    }
    const pages = Math.max(data.pages, 1);
    $("#page-info").textContent = `Page ${data.page} of ${pages} · ${count.format(data.total)} claims`;
    $("#prev-page").disabled = data.page <= 1;
    $("#next-page").disabled = data.page >= pages;
  }

  // --- events ------------------------------------------------------------------

  const form = $("#filters");
  form.addEventListener("change", (e) => {
    // Keep the two date pickers from offering an inverted range.
    if (e.target.name === "from") $("#f-to").min = e.target.value;
    if (e.target.name === "to") $("#f-from").max = e.target.value;
    reloadAll();
  });
  form.addEventListener("submit", (e) => e.preventDefault());
  form.addEventListener("reset", () => {
    $("#f-to").min = "";
    $("#f-from").max = "";
    setTimeout(reloadAll); // after the browser has cleared the fields
  });

  function bindToggle(attr, key, render) {
    const buttons = $$(`[data-${attr}]`);
    for (const btn of buttons) {
      btn.addEventListener("click", () => {
        view[key] = btn.dataset[attr];
        for (const b of buttons) b.setAttribute("aria-pressed", String(b === btn));
        render();
      });
    }
  }
  bindToggle("monthly", "monthlyMeasure", renderMonthly);
  bindToggle("lr", "lrGroup", renderLossRatio);

  for (const th of $$("th[data-sort]")) {
    th.querySelector("button").addEventListener("click", () => {
      const key = th.dataset.sort;
      if (view.sort === key) {
        view.order = view.order === "asc" ? "desc" : "asc";
      } else {
        view.sort = key;
        view.order = th.classList.contains("num") || key.endsWith("_date") ? "desc" : "asc";
      }
      view.page = 1;
      loadTable();
    });
  }

  $("#per-page").addEventListener("change", (e) => {
    view.perPage = Number(e.target.value);
    view.page = 1;
    loadTable();
  });
  $("#prev-page").addEventListener("click", () => { view.page -= 1; loadTable(); });
  $("#next-page").addEventListener("click", () => { view.page += 1; loadTable(); });

  // Chart colours come from CSS tokens, so redraw when the colour scheme flips.
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    renderMonthly();
    renderLossRatio();
  });

  setStatus("");
  reloadAll();
})();
