"""Tests for the ROI API endpoints (engine exposed over HTTP)."""

from fastapi.testclient import TestClient

from app.main import app
from app.services.economics import FIELD_META, PRESETS


def test_calculate_returns_camelcase_shape_and_known_numbers():
    """POST /calculate with defaults returns the camelCase RoiResult shape."""
    with TestClient(app) as client:
        r = client.post("/api/roi/calculate", json={})
    assert r.status_code == 200
    body = r.json()
    # camelCase keys present
    for key in ("availableKw", "gpuCount", "minerCount", "aiRevenueDay",
                "btcRevenueDay", "dailyNet", "paybackMonths", "irr", "npv",
                "co2eTonnesPerYear", "upliftDaily", "upliftPct", "softwareFeeMonthly"):
        assert key in body, key
    # known reference numbers (hand-checked in test_economics.py)
    assert body["gpuCount"] == 299
    assert body["minerCount"] == 150
    assert body["dailyNet"] == round(11079.25, 2) or abs(body["dailyNet"] - 11079.25) < 2
    assert body["aiRevenueDay"] > body["btcRevenueDay"]
    assert body["upliftDaily"] > 0


def test_calculate_accepts_camelcase_overrides():
    """Slider overrides come in camelCase; more gas -> more GPUs."""
    with TestClient(app) as client:
        base = client.post("/api/roi/calculate", json={}).json()
        more = client.post("/api/roi/calculate", json={"gasMcfPerDay": 4000}).json()
    assert more["availableKw"] > base["availableKw"]
    assert more["gpuCount"] > base["gpuCount"]


def test_defaults_lists_every_slider_with_citation_and_presets():
    """GET /defaults exposes each slider (default/range/unit/cite) + region presets."""
    with TestClient(app) as client:
        body = client.get("/api/roi/defaults").json()
    fields = body["fields"]
    assert len(fields) == len(FIELD_META)
    for f in fields:
        assert set(f) >= {"key", "value", "min", "max", "step", "unit", "label", "cite"}
        assert f["cite"]  # non-empty citation string
    # camelCase keys (e.g. gasMcfPerDay), value within range
    keys = {f["key"] for f in fields}
    assert "gasMcfPerDay" in keys and "hashpriceUsdPerPhDay" in keys
    # presets: all four regions, with camelCase override maps
    presets = body["presets"]
    assert len(presets) == len(PRESETS)
    labels = {p["label"] for p in presets}
    assert {"Permian (TX)", "Bakken (ND)", "Vaca Muerta (AR)", "Oman/UAE"} <= labels
    permian = next(p for p in presets if p["id"] == "permian")
    assert "gasMcfPerDay" in permian["overrides"]


def test_bad_input_returns_422():
    """Out-of-range or invalid inputs are rejected with 422."""
    with TestClient(app) as client:
        assert client.post("/api/roi/calculate", json={"gasMcfPerDay": -5}).status_code == 422
        assert client.post("/api/roi/calculate", json={"discountRate": 2.0}).status_code == 422
        bad_mode = client.post("/api/roi/calculate", json={"softwareFeeMode": "bogus"})
        assert bad_mode.status_code == 422
