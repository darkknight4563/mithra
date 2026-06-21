"""REST + WebSocket contract tests against the wired FastAPI app.

These assert shape/camelCase contract rather than exact live counts, since the
control loop runs in the background and may mutate the fleet between calls.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_plc_latest_is_camelcase():
    with TestClient(app) as client:
        body = client.get("/api/plc/latest").json()
    reading = body["reading"]
    assert set(reading) == {"timestamp", "generatorKw", "status"}
    assert isinstance(reading["generatorKw"], (int, float))


def test_miners_list_shape():
    with TestClient(app) as client:
        body = client.get("/api/miners").json()
    miners = body["miners"]
    assert len(miners) == 10
    expected = {"id", "ip", "priority", "status", "powerKw", "hashrateMhs", "lastSeen"}
    assert expected <= set(miners[0])


def test_controller_state_superset():
    with TestClient(app) as client:
        body = client.get("/api/controller/state").json()
    state = body["state"]
    # Both the written-spec names and the live-frontend names are present.
    for key in (
        "mode", "bufferFactor", "hysteresis",
        "loopInterval", "loopIntervalSec",
        "availableKw", "availablePowerKw",
        "miningLoadKw", "currentLoadKw",
        "activeMiners", "latestPlcKw",
    ):
        assert key in state, key


def test_toggle_miner_power():
    with TestClient(app) as client:
        body = client.post("/api/miners/9/power", json={"state": "ON"}).json()
        assert body["miner"]["id"] == 9
        assert body["miner"]["status"] in ("ON", "BOOTING")
        off = client.post("/api/miners/9/power", json={"state": "OFF"}).json()
        assert off["miner"]["status"] == "OFF"
        assert off["miner"]["hashrateMhs"] is None


def test_toggle_unknown_miner_404():
    with TestClient(app) as client:
        assert client.post("/api/miners/999/power", json={"state": "ON"}).status_code == 404


def test_scenario_and_sim_endpoints():
    with TestClient(app) as client:
        assert client.post("/api/scenario", json={"scenario": "ramp_up", "enabled": True}).json()["success"]
        assert client.post("/api/sim/ramp").json()["success"]
        assert client.post("/api/sim/drop?pct=20").json()["success"]
        assert client.post("/api/sim/noisy?on=true").json()["success"]


def test_logs_endpoint():
    with TestClient(app) as client:
        body = client.get("/api/logs?limit=5").json()
    assert "items" in body
    assert len(body["items"]) <= 5


def test_config_update():
    with TestClient(app) as client:
        body = client.put(
            "/api/controller/config", json={"bufferFactor": 0.85, "loopIntervalSec": 7}
        ).json()
    assert body["state"]["bufferFactor"] == 0.85
    assert body["state"]["loopInterval"] == 7
    assert body["state"]["loopIntervalSec"] == 7


def test_websocket_initial_snapshot():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            seen_types = set()
            for _ in range(6):
                msg = websocket.receive_json()
                seen_types.add(msg["type"])
            # Both message-type styles are broadcast (superset).
            assert "controller:state" in seen_types
            assert "controller_state_update" in seen_types
            assert "miners:update" in seen_types
