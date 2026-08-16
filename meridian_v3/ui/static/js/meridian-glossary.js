/* MERIDIAN V3 — glossary tooltips and SVG infographics.
 *
 * Kept separate from meridian-v3.js (charts, tables, auto-refresh) so the
 * two concerns stay independently readable. No build step, no dependencies.
 */
(() => {
  "use strict";

  /* ─── Glossary tooltips ──────────────────────────────────────────────────
     The desk is dense with jargon — ATR, confluence, TDS, walk-forward. Any
     known term is wrapped in a focusable button and explained in plain
     English on hover.

     Keyboard parity is deliberate, not decorative. Hover-only is a Critical
     accessibility anti-pattern, so the tip opens on focus as well as hover
     and closes on Escape — WCAG 1.4.13 "content on hover or focus" wants it
     hoverable, dismissible and persistent. */

  let glossary = null;
  let tipEl = null;

  const ensureTipEl = () => {
    if (tipEl) return tipEl;
    tipEl = document.createElement("div");
    tipEl.className = "tip";
    tipEl.id = "glossary-tip";
    tipEl.setAttribute("role", "tooltip");
    document.body.appendChild(tipEl);
    return tipEl;
  };

  const placeTip = (x, y) => {
    const el = ensureTipEl();
    const pad = 14;
    const r = el.getBoundingClientRect();
    let left = x + pad;
    let top = y + pad;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - pad;
    if (top + r.height > window.innerHeight - 8) top = y - r.height - pad;
    el.style.left = Math.max(8, left) + "px";
    el.style.top = Math.max(8, top) + "px";
  };

  const showTip = (term, x, y) => {
    const entry = glossary && glossary[term];
    if (!entry) return;
    const el = ensureTipEl();
    el.replaceChildren();
    const label = document.createElement("p");
    label.className = "tip-label";
    label.textContent = entry.label;
    const body = document.createElement("p");
    body.className = "tip-body";
    body.textContent = entry.body;
    const hint = document.createElement("span");
    hint.className = "tip-hint";
    hint.textContent = "Esc to dismiss";
    el.append(label, body, hint);
    el.setAttribute("data-show", "");
    placeTip(x, y);
  };

  const hideTip = () => {
    if (tipEl) tipEl.removeAttribute("data-show");
  };

  /* Longest-first, so "round trip" wins over "trip" and "walk-forward" over
     "walk". Word-boundary matched, case-insensitive. */
  const buildTermRegex = (terms) => {
    const parts = terms
      .slice()
      .sort((a, b) => b.length - a.length)
      .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    return new RegExp("\\b(" + parts.join("|") + ")\\b", "gi");
  };

  /* Never rewrite text inside a control, a link, or an existing term: that
     would mangle form values or nest a button inside a button. */
  const SKIP = new Set([
    "SCRIPT", "STYLE", "BUTTON", "A", "INPUT", "TEXTAREA",
    "SELECT", "OPTION", "SVG", "PATH", "CANVAS",
  ]);

  const markTerms = (root, re) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        let p = node.parentElement;
        while (p && p !== root) {
          if (SKIP.has(p.tagName) || p.classList.contains("term")) return NodeFilter.FILTER_REJECT;
          p = p.parentElement;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    const targets = [];
    let node;
    while ((node = walker.nextNode())) {
      re.lastIndex = 0;
      if (re.test(node.nodeValue)) targets.push(node);
    }

    targets.forEach((textNode) => {
      const text = textNode.nodeValue;
      const frag = document.createDocumentFragment();
      let last = 0;
      let m;
      re.lastIndex = 0;
      while ((m = re.exec(text))) {
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        const b = document.createElement("button");
        b.type = "button";
        b.className = "term";
        b.textContent = m[0];
        b.dataset.term = m[0].toLowerCase();
        b.setAttribute("aria-describedby", "glossary-tip");
        frag.appendChild(b);
        last = m.index + m[0].length;
      }
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      textNode.parentNode.replaceChild(frag, textNode);
    });
  };

  const wireGlossary = async () => {
    try {
      const res = await fetch("/api/glossary");
      if (!res.ok) return;
      glossary = await res.json();
    } catch (err) {
      return; // No glossary is a missing nicety, never a broken page.
    }
    const terms = Object.keys(glossary);
    if (!terms.length) return;
    const re = buildTermRegex(terms);
    document.querySelectorAll("[data-glossary]").forEach((root) => markTerms(root, re));

    document.addEventListener("mouseover", (e) => {
      const t = e.target.closest && e.target.closest(".term");
      if (t) showTip(t.dataset.term, e.clientX, e.clientY);
    });
    document.addEventListener("mousemove", (e) => {
      if (!tipEl || !tipEl.hasAttribute("data-show")) return;
      if (e.target.closest && e.target.closest(".term")) placeTip(e.clientX, e.clientY);
    });
    document.addEventListener("mouseout", (e) => {
      if (e.target.closest && e.target.closest(".term")) hideTip();
    });
    document.addEventListener("focusin", (e) => {
      const t = e.target.closest && e.target.closest(".term");
      if (!t) return;
      const r = t.getBoundingClientRect();
      showTip(t.dataset.term, r.left, r.bottom);
    });
    document.addEventListener("focusout", (e) => {
      if (e.target.closest && e.target.closest(".term")) hideTip();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") hideTip();
    });
  };

  /* ─── SVG infographics ───────────────────────────────────────────────────
     Hand-rolled rather than pulling in a charting library: there's no build
     step here, and the chart guidance recommends custom SVG for gauges and
     bullets specifically. Every graphic is paired with a readable number in
     the markup, because color alone is never an acceptable sole channel. */

  const NS = "http://www.w3.org/2000/svg";

  const sparkline = (el) => {
    let pts;
    try {
      pts = JSON.parse(el.dataset.spark || "[]").map(Number).filter((n) => !Number.isNaN(n));
    } catch (err) {
      return;
    }
    if (pts.length < 2) return;
    const w = el.clientWidth || 220;
    const h = el.clientHeight || 30;
    const lo = Math.min.apply(null, pts);
    const hi = Math.max.apply(null, pts);
    const span = hi - lo || 1;
    const step = w / (pts.length - 1);
    const coords = pts.map((v, i) => [i * step, h - ((v - lo) / span) * (h - 4) - 2]);
    const d = coords
      .map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1))
      .join(" ");
    const rising = pts[pts.length - 1] >= pts[0];
    const stroke = rising ? "var(--up)" : "var(--down)";

    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");

    const area = document.createElementNS(NS, "path");
    area.setAttribute("d", d + " L " + w + " " + h + " L 0 " + h + " Z");
    area.setAttribute("fill", stroke);
    area.setAttribute("opacity", "0.10");

    const line = document.createElementNS(NS, "path");
    line.setAttribute("d", d);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", stroke);
    line.setAttribute("stroke-width", "1.4");

    svg.append(area, line);
    el.replaceChildren(svg);
  };

  const gauge = (el) => {
    const pct = Math.max(0, Math.min(1, parseFloat(el.dataset.gauge || "0")));
    const warn = parseFloat(el.dataset.warn || "0.4");
    const bad = parseFloat(el.dataset.bad || "0.75");
    const size = 120;
    const r = 48;
    const arcLen = Math.PI * r;

    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + size + " " + size * 0.62);
    svg.setAttribute("width", String(size));
    svg.setAttribute("class", "gauge");
    svg.setAttribute("aria-hidden", "true");

    const arc = (cls, offset) => {
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", "M 12 " + size * 0.56 + " A " + r + " " + r + " 0 0 1 " + (size - 12) + " " + size * 0.56);
      p.setAttribute("fill", "none");
      p.setAttribute("stroke-width", "8");
      p.setAttribute("stroke-linecap", "round");
      p.setAttribute("class", cls);
      p.setAttribute("stroke-dasharray", String(arcLen));
      p.setAttribute("stroke-dashoffset", String(offset));
      return p;
    };

    svg.appendChild(arc("gauge-track", 0));
    const tone = pct >= bad ? "gauge-fill bad" : pct >= warn ? "gauge-fill warn" : "gauge-fill";
    svg.appendChild(arc(tone, arcLen * (1 - pct)));
    el.replaceChildren(svg);
  };

  const mountInfographics = () => {
    document.querySelectorAll("[data-spark]").forEach(sparkline);
    document.querySelectorAll("[data-gauge]").forEach(gauge);
  };

  document.addEventListener("DOMContentLoaded", () => {
    mountInfographics();
    wireGlossary();
  });
})();
