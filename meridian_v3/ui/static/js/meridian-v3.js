(() => {
  const THEME_KEY = "meridian-v3-theme";
  const charts = [];
  const isLight = () => document.body.classList.contains("light");

  const applyStoredTheme = () => {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light") document.body.classList.add("light");
    if (stored === "dark") document.body.classList.remove("light");
  };

  const palette = () => {
    const light = isLight();
    return {
      bg: light ? "#f7f2e8" : "#0e131b",
      text: light ? "#2a261f" : "#e7e2d6",
      grid: light ? "#ddd6c8" : "#1c2433",
      up: "#4A9B7F",
      down: "#C46B6B",
      gold: "#C4A35A",
      tipBg: light ? "rgba(247,242,232,0.96)" : "rgba(14,19,27,0.94)",
      tipBorder: light ? "#d9d0c0" : "#1c2433",
    };
  };

  const ensureOverlay = (el) => {
    let canvas = el.querySelector("canvas.chart-overlay");
    if (!canvas) {
      el.style.position = "relative";
      canvas = document.createElement("canvas");
      canvas.className = "chart-overlay";
      canvas.style.cssText = "position:absolute;inset:0;pointer-events:none;z-index:2;";
      el.appendChild(canvas);
    }
    return canvas;
  };

  const ensureTip = (el) => {
    let tip = el.querySelector(".chart-tip");
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "chart-tip";
      tip.hidden = true;
      el.appendChild(tip);
    }
    return tip;
  };

  const paintOverlays = (el, chart, series, data) => {
    const canvas = ensureOverlay(el);
    const dpr = window.devicePixelRatio || 1;
    const width = el.clientWidth;
    const height = el.clientHeight;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const ts = chart.timeScale();
    (data.windows || []).forEach((win) => {
      const x1 = ts.timeToCoordinate(win.start);
      const x2 = ts.timeToCoordinate(win.end);
      if (x1 == null || x2 == null) return;
      ctx.fillStyle = win.color || "rgba(74,155,127,0.10)";
      ctx.fillRect(Math.min(x1, x2), 0, Math.abs(x2 - x1), height);
    });
    (data.zones || []).forEach((zone) => {
      const y1 = series.priceToCoordinate(zone.high);
      const y2 = series.priceToCoordinate(zone.low);
      if (y1 == null || y2 == null) return;
      ctx.fillStyle = zone.color || "rgba(196,163,90,0.12)";
      ctx.fillRect(0, Math.min(y1, y2), width, Math.abs(y2 - y1));
    });
  };

  // 2.B.7 — .chart-box starts completely blank; show an immediate skeleton
  // and replace it with a plain failure message if the fetch throws or
  // comes back non-OK, instead of leaving a dead panel forever.
  const setChartMessage = (el, text) => {
    let msg = el.querySelector(".skeleton-state");
    if (!msg) {
      msg = document.createElement("div");
      msg.className = "skeleton-state";
      el.appendChild(msg);
    }
    msg.textContent = text;
    return msg;
  };

  const mountChart = async (el) => {
    if (!el) return;
    const symbol = el.dataset.symbol;
    if (!symbol) return;
    // A redraw (e.g. the theme toggle re-mounts every open chart) starts
    // from an el that already has a working chart. If the refetch fails,
    // prefer leaving that still-valid chart on screen over blanking it out
    // from under the user — only the true first-load (no chart yet) earns
    // the full loading/failure overlay.
    const isFirstMount = !el._chart;
    if (!window.LightweightCharts) {
      if (isFirstMount) setChartMessage(el, "Chart library did not load. Reload the page.");
      return;
    }
    if (isFirstMount) setChartMessage(el, "Loading chart…");
    let res;
    try {
      res = await fetch(`/api/chart/${encodeURIComponent(symbol)}`);
    } catch (err) {
      if (isFirstMount) setChartMessage(el, "Couldn't load the chart. Check your connection and reload.");
      else console.warn("chart redraw failed, keeping the existing chart", err);
      return;
    }
    if (!res.ok) {
      if (isFirstMount) setChartMessage(el, "Couldn't load the chart for this name yet.");
      else console.warn(`chart redraw failed with ${res.status}, keeping the existing chart`);
      return;
    }
    const data = await res.json();
    const skeleton = el.querySelector(".skeleton-state");
    if (skeleton) skeleton.remove();
    const colors = palette();
    if (el._chart) {
      el._chart.remove();
      el._chart = null;
    }
    const chart = LightweightCharts.createChart(el, {
      width: el.clientWidth,
      height: Number(el.dataset.height || 380),
      layout: { background: { color: colors.bg }, textColor: colors.text, fontFamily: "IBM Plex Sans, Segoe UI, sans-serif", fontSize: 14 },
      grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.grid },
      timeScale: { borderColor: colors.grid, rightOffset: 4 },
      crosshair: { mode: 0 },
    });
    const candles = chart.addCandlestickSeries({
      upColor: colors.up, downColor: colors.down,
      borderUpColor: colors.up, borderDownColor: colors.down,
      wickUpColor: colors.up, wickDownColor: colors.down,
    });
    candles.setData((data.candles || []).map((row) => ({
      time: row.time, open: row.open, high: row.high, low: row.low, close: row.close,
    })));
    if (data.markers && data.markers.length) candles.setMarkers(data.markers);
    if (data.signal && data.signal.length) {
      const line = chart.addLineSeries({ color: colors.gold, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      line.setData(data.signal);
    }
    (data.levels || []).forEach((level) => {
      if (!level.price) return;
      candles.createPriceLine({ price: level.price, color: level.color || colors.gold, lineWidth: 1, lineStyle: 2, title: level.title || "" });
    });
    const draw = () => paintOverlays(el, chart, candles, data);
    chart.timeScale().subscribeVisibleLogicalRangeChange(draw);
    requestAnimationFrame(draw);
    const tip = ensureTip(el);
    const markerAt = (time) => (data.markers || []).find((item) => String(item.time) === String(time));
    chart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || !param.point) { tip.hidden = true; return; }
      const bar = param.seriesData.get(candles);
      if (!bar) { tip.hidden = true; return; }
      const mark = markerAt(param.time);
      tip.hidden = false;
      tip.style.background = colors.tipBg;
      tip.style.borderColor = colors.tipBorder;
      tip.style.left = `${param.point.x + 12}px`;
      tip.style.top = `${param.point.y + 12}px`;
      tip.innerHTML = `<strong>${param.time}</strong>
        <span>Open ${bar.open.toFixed(2)}</span>
        <span>High ${bar.high.toFixed(2)}</span>
        <span>Low ${bar.low.toFixed(2)}</span>
        <span>Close ${bar.close.toFixed(2)}</span>
        ${mark ? `<span>${mark.text}</span>` : ""}`;
    });
    el._chart = chart;
    charts.push({ redraw: () => mountChart(el) });
  };

  // 2.B.7 — #review-root already ships with "Loading the review…" as its
  // initial server-rendered text (review.html), so — unlike .chart-box —
  // this one is not actually blank while awaiting fetch; that existing text
  // is a sufficient loading state and is left as-is. The gap here was the
  // failure path: a thrown fetch (network error) fell through uncaught and
  // left "Loading the review…" on screen forever. Add the same "couldn't
  // load" handling mountChart now has.
  const mountReview = async (el) => {
    const symbol = el.dataset.symbol;
    if (!symbol) return;
    let res;
    try {
      res = await fetch(`/api/review/${encodeURIComponent(symbol)}`);
    } catch (err) {
      el.textContent = "Couldn't load the review. Check your connection and reload.";
      return;
    }
    if (!res.ok) { el.textContent = "No review yet."; return; }
    const data = await res.json();
    const r = data.review || {};
    el.innerHTML = `
      <p class="kicker">${r.title || ""}</p>
      ${(r.status || []).map((line) => `<p>${line}</p>`).join("")}
      <p>${r.daily_pnl || ""}</p>
      <p>${r.gamma_scalp_pnl || ""}</p>
      <p>${r.suggestion || ""}</p>
      <p class="meta">${(r.choices || []).join(" · ")}</p>
    `;
  };

  // 2.B.5/6 — the desk-mutating POST routes (/desk/cycle, /desk/seed,
  // /desk/auto) are intentionally left as ordinary form POST -> 303 redirect
  // endpoints server-side (no API redesign). What changes here is only how
  // the *client* calls them: fetch() follows the redirect automatically and
  // hands back the final rendered Command-page HTML (the same bytes a normal
  // navigation would have produced). We parse that HTML and swap a fixed set
  // of stable-id elements (notice banner, mast-meta badges, Command/Book
  // panels) from the parsed document into the live one, instead of letting
  // the browser navigate. Any id in PATCH_IDS that doesn't exist on the
  // *current* page (e.g. Book-specific panels while sitting on Command) or
  // doesn't exist in the *fetched* page (e.g. Command-specific panels when
  // the fetch's redirect target is always "/") is simply skipped — see
  // swapById.
  const PATCH_IDS = [
    "notice-slot",
    "badge-ack",
    "badge-live-armed",
    "badge-auto",
    "desk-actions",
    "panel-paper-auto",
    "panel-paper-book",
    "panel-live-book",
    "panel-open-clips",
    "panel-latest-decisions",
    "panel-charges",
    "panel-paper-open",
    "panel-live-open",
    "panel-paper-unreconciled",
    "panel-settled-paper",
    "panel-settled-live",
    "panel-fills",
  ];

  const swapById = (doc, id) => {
    const next = doc.getElementById(id);
    const current = document.getElementById(id);
    if (next && current) current.replaceWith(next);
  };

  const patchFromHtml = (html) => {
    const doc = new DOMParser().parseFromString(html, "text/html");
    if (doc.body) {
      document.body.dataset.autoOn = doc.body.dataset.autoOn === "true" ? "true" : "false";
    }
    PATCH_IDS.forEach((id) => swapById(doc, id));
  };

  // 2.B.6 — poll Command ("/") and Book ("/book") on a short interval while
  // paper auto is actually on, so the desk shows near-live state without a
  // manual reload. "Actually on" is read from data-auto-on on <body>, which
  // the server stamps from the same `auto_on` value the AUTO ON/OFF badge
  // already uses (base.html) — never re-derived independently in JS — and is
  // re-synced from every patched response, including these polls themselves.
  let refreshTimer = null;

  const shouldPoll = () =>
    document.body.dataset.autoOn === "true" &&
    (window.location.pathname === "/" || window.location.pathname === "/book");

  const refreshTick = async () => {
    if (document.visibilityState === "hidden") return;
    if (!shouldPoll()) return; // paper auto may have turned off since the last tick
    try {
      const res = await fetch(window.location.href, { headers: { "X-Meridian-Refresh": "1" } });
      if (!res.ok) return;
      const html = await res.text();
      patchFromHtml(html);
    } catch (err) {
      // transient network hiccup — the next tick will retry
    }
  };

  const startAutoRefresh = () => {
    if (refreshTimer) return;
    refreshTimer = window.setInterval(refreshTick, 12000);
  };

  const stopAutoRefresh = () => {
    if (refreshTimer) {
      window.clearInterval(refreshTimer);
      refreshTimer = null;
    }
  };

  // Re-evaluate after every state-changing patch (a Start/Stop paper auto
  // click) and on visibility changes, so the poll starts/stops promptly
  // rather than waiting for the (harmless, but slower) no-op-until-next-tick
  // path inside refreshTick to catch up.
  const syncAutoRefresh = () => {
    if (shouldPoll()) startAutoRefresh();
    else stopAutoRefresh();
  };

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") stopAutoRefresh();
    else syncAutoRefresh();
  });

  // 2.B.5 — intercept submits on the three desk-mutating forms (they live in
  // both base.html's always-visible rail and command.html's hero section,
  // and command.html's own forms get replaced wholesale by patchFromHtml
  // when #desk-actions is swapped) via delegation on `document`, so newly
  // swapped-in form nodes are covered without re-wiring listeners by hand.
  const DESK_ACTION_PATHS = new Set(["/desk/cycle", "/desk/seed", "/desk/auto"]);

  const deskFormPath = (form) => {
    try {
      return new URL(form.action, window.location.origin).pathname;
    } catch (err) {
      return form.getAttribute("action") || "";
    }
  };

  const handleDeskFormSubmit = async (form) => {
    const btn = form.querySelector("button[type=submit], button:not([type])");
    const originalLabel = btn ? btn.textContent : "";
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Working…";
    }
    try {
      const body = new FormData(form);
      const res = await fetch(form.action, { method: (form.method || "POST").toUpperCase(), body });
      if (!res.ok) throw new Error(`desk action failed: ${res.status}`);
      const html = await res.text();
      patchFromHtml(html);
      syncAutoRefresh();
    } catch (err) {
      // Progressive-enhancement fallback: a thrown fetch (offline, CORS,
      // unexpected server error) falls back to a normal full-page submit
      // rather than silently doing nothing. preventDefault() already ran, so
      // this manual submit is what actually navigates.
      form.submit();
      return;
    }
    if (btn) {
      btn.disabled = false;
      btn.textContent = originalLabel;
    }
  };

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.method.toUpperCase() !== "POST") return;
    if (!DESK_ACTION_PATHS.has(deskFormPath(form))) return;
    event.preventDefault();
    handleDeskFormSubmit(form);
  });

  // 2.A.1 — "Arm live" used to submit instantly with no confirmation. The
  // adapter-connected fact is computed server-side (routes.py:safety_page,
  // sourced from execution/brokers/plugin.py:get_live_broker) and handed to
  // this form as a data attribute — this code only reads it, it never
  // guesses whether a broker is registered.
  const wireArmConfirm = () => {
    document.querySelectorAll("form[data-confirm-arm]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        const adapter = form.dataset.adapterName || "";
        const message = adapter
          ? `Arm live trading on the ₹50,000 book with the '${adapter}' broker adapter connected?`
          : "Arm live trading on the ₹50,000 book? No broker adapter is registered — orders will still be rejected until one is.";
        if (!window.confirm(message)) {
          event.preventDefault();
        }
      });
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    applyStoredTheme();
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.body.classList.toggle("light");
        localStorage.setItem(THEME_KEY, isLight() ? "light" : "dark");
        charts.forEach((item) => item.redraw());
      });
    });
    document.querySelectorAll(".chart-box").forEach(mountChart);
    document.querySelectorAll("#review-root").forEach(mountReview);
    wireArmConfirm();
    syncAutoRefresh();
  });
})();
