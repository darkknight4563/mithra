"""REST + WebSocket contract tests against the wired FastAPI app.

These assert shape/camelCase contract rather than exact live counts, since the
control loop runs in the background and may mutate the fleet between calls.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.services.miners import NUM_MINERS


def test_health():
    """GET /health returns the ok status payload."""
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_plc_latest_is_camelcase():
    """GET /api/plc/latest returns a camelCase reading envelope."""
    with TestClient(app) as client:
        body = client.get("/api/plc/latest").json()
    reading = body["reading"]
    assert set(reading) == {"timestamp", "generatorKw", "status"}
    assert isinstance(reading["generatorKw"], (int, float))


def test_miners_list_shape():
    """GET /api/miners returns 10 miners with the expected camelCase fields."""
    with TestClient(app) as client:
        body = client.get("/api/miners").json()
    miners = body["miners"]
    assert len(miners) == NUM_MINERS
    expected = {"id", "ip", "priority", "status", "powerKw", "hashrateMhs", "lastSeen"}
    assert expected <= set(miners[0])


def test_controller_state_superset():
    """GET /api/controller/state emits both spec and frontend field names."""
    with TestClient(app) as client:
        body = client.get("/api/controller/state").json()
    state = body["state"]
    for key in (
        "mode", "bufferFactor", "hysteresis",
        "loopInterval", "loopIntervalSec",
        "availableKw", "availablePowerKw",
        "miningLoadKw", "currentLoadKw",
        "activeMiners", "latestPlcKw",
    ):
        assert key in state, key


def test_toggle_miner_power():
    """POST /api/miners/{id}/power changes a miner's state (ON then OFF)."""
    with TestClient(app) as client:
        body = client.post("/api/miners/9/power", json={"state": "ON"}).json()
        assert body["miner"]["id"] == 9
        assert body["miner"]["status"] in ("ON", "BOOTING")
        off = client.post("/api/miners/9/power", json={"state": "OFF"}).json()
        assert off["miner"]["status"] == "OFF"
        assert off["miner"]["hashrateMhs"] is None


def test_toggle_unknown_miner_404():
    """Toggling a non-existent miner returns 404."""
    with TestClient(app) as client:
        resp = client.post("/api/miners/999/power", json={"state": "ON"})
        assert resp.status_code == 404


def test_scenario_and_sim_endpoints():
    """Both the spec scenario route and the frontend sim aliases succeed."""
    with TestClient(app) as client:
        spec = client.post("/api/scenario", json={"scenario": "ramp_up", "enabled": True})
        assert spec.json()["success"]
        assert client.post("/api/sim/ramp").json()["success"]
        assert client.post("/api/sim/drop?pct=20").json()["success"]
        assert client.post("/api/sim/noisy?on=true").json()["success"]


def test_logs_endpoint():
    """GET /api/logs returns an items envelope honouring the limit."""
    with TestClient(app) as client:
        body = client.get("/api/logs?limit=5").json()
    assert "items" in body
    assert len(body["items"]) <= 5


def test_config_update():
    """PUT /api/controller/config updates live params in both naming styles."""
    with TestClient(app) as client:
        body = client.put(
            "/api/controller/config", json={"bufferFactor": 0.85, "loopIntervalSec": 7}
        ).json()
    assert body["state"]["bufferFactor"] == 0.85
    assert body["state"]["loopInterval"] == 7
    assert body["state"]["loopIntervalSec"] == 7


def test_websocket_initial_snapshot():
    """Connecting to /ws yields an initial snapshot in both message styles."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            seen_types = set()
            for _ in range(6):
                msg = websocket.receive_json()
                seen_types.add(msg["type"])
            assert "controller:state" in seen_types
            assert "controller_state_update" in seen_types
            assert "miners:update" in seen_types
