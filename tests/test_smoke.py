from fastapi.testclient import TestClient

from meridian_v3.storage.seed import seed_demo


def test_app_health_and_pages(session):
    seed_demo(session, reset=True)
    session.commit()
    from meridian_v3.app import create_app

    client = TestClient(create_app())
    assert client.get("/api/health").json()["ok"] is True
    for path in ("/", "/signals", "/book", "/review", "/chart", "/import", "/holdings", "/watch", "/safety", "/help"):
        res = client.get(path)
        assert res.status_code == 200, path
    chart = client.get("/api/chart/RELIANCE")
    assert chart.status_code == 200
    assert "candles" in chart.json()
    review = client.get("/api/review/NIFTY")
    assert review.status_code == 200
    body = review.json()
    assert "(not an order)" in body["review"]["title"].lower()


def test_seed_and_cycle(session):
    from meridian_v3.pipeline import run_cycle

    seed_demo(session, reset=True)
    result = run_cycle(session)
    assert result["decided"] >= 1
    assert result["paper_opened"] >= 1
