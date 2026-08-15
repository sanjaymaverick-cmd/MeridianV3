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

  const mountChart = async (el) => {
    if (!el || !window.LightweightCharts) return;
    const symbol = el.dataset.symbol;
    if (!symbol) return;
    const res = await fetch(`/api/chart/${encodeURIComponent(symbol)}`);
    if (!res.ok) return;
    const data = await res.json();
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

  const mountReview = async (el) => {
    const symbol = el.dataset.symbol;
    if (!symbol) return;
    const res = await fetch(`/api/review/${encodeURIComponent(symbol)}`);
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
  });
})();
