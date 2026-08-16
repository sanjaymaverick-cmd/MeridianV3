"""The hover glossary and the Command page infographics.

The glossary exists because this desk is dense with jargon — ATR, confluence,
walk-forward, TDS — and the owner has to be able to read its reasoning without
already knowing the vocabulary. So the definitions are asserted to be *plain*,
not merely present.
"""

from __future__ import annotations

from meridian_v3.ui.glossary import GLOSSARY, glossary_payload


def _client(session):
    """Same construction the smoke test uses: seed, then build the app."""
    from fastapi.testclient import TestClient

    from meridian_v3.app import create_app
    from meridian_v3.storage.seed import seed_demo

    seed_demo(session, reset=True)
    session.commit()
    return TestClient(create_app())


def test_payload_shape_matches_what_the_browser_expects():
    payload = glossary_payload()
    assert payload, "glossary must not be empty"
    for term, entry in payload.items():
        assert term == term.lower(), f"{term!r} must be lowercase for case-insensitive lookup"
        assert entry["label"], f"{term} needs a label"
        assert entry["body"], f"{term} needs a body"


def test_the_expensive_ideas_are_all_explained():
    """Every concept that has actually cost this book money or confused its
    owner must be in here — cost structure, the safety rails, and the
    validation vocabulary."""
    must_cover = {
        "tds", "round trip", "slippage", "spread", "brokerage",
        "drawdown", "kill switch", "atr", "kelly",
        "walk-forward", "robustness", "out-of-sample",
        "confluence", "confidence", "edge", "meta-label",
        "paper", "live", "armed", "scratch",
    }
    missing = must_cover - set(GLOSSARY)
    assert not missing, f"undefined jargon: {sorted(missing)}"


def test_definitions_are_written_in_plain_language():
    """A definition that needs a definition is useless. Keep them short and
    free of the acronyms they are supposed to be explaining."""
    for term, (label, body) in GLOSSARY.items():
        assert len(body) <= 260, f"{term}: {len(body)} chars is too long for a tooltip"
        assert body.strip().endswith("."), f"{term}: should read as a sentence"
        assert len(label) <= 30, f"{term}: label too long"


def test_tds_explains_why_crypto_is_expensive():
    """The single most consequential cost fact for this book: 1% per leg, so
    a crypto round trip needs >2% of movement. If the tooltip doesn't say
    that, it isn't earning its place."""
    body = GLOSSARY["tds"][1]
    assert "1%" in body
    assert "2%" in body


def test_glossary_endpoint_serves_the_payload(session):
    client = _client(session)
    res = client.get("/api/glossary")
    assert res.status_code == 200
    data = res.json()
    assert data["atr"]["label"] == "ATR"
    assert "cache-control" in {k.lower() for k in res.headers}


def test_command_page_renders_the_infographics(session):
    """The Command page must ship the markup the JS hydrates: a sparkline
    payload, a gauge ratio, and one bullet row per market."""
    client = _client(session)
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert 'data-glossary' in html, "glossary scanner needs an opted-in root"
    assert 'data-gauge=' in html
    assert 'class="bullet"' in html
    assert "meridian-glossary.js" in html


def test_cost_bars_rank_crypto_as_the_most_expensive_venue():
    """The bullet chart's whole job is making the ~10x cost gap between
    crypto and equity visible at a glance."""
    from meridian_v3.config import Settings
    from meridian_v3.ui.routes import _cost_vs_target_bars

    rows = {r["market"]: r for r in _cost_vs_target_bars(Settings())}
    assert rows["crypto_spot"]["cost"] > rows["equity_cash"]["cost"] * 4
    assert rows["equity_cash"]["cost"] > rows["global_commodities"]["cost"]
    # The bar is the required target, always taller than the cost marker.
    for market, row in rows.items():
        assert row["need"] > row["cost"], f"{market}: target must exceed cost"
        assert 0 <= row["marker_pct"] <= 100
        assert 0 <= row["fill_pct"] <= 100
