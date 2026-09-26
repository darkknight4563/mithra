# Mithra — load control for compute behind constrained power

**Mithra keeps a fleet of miners (or any interruptible compute load) inside the
operating window of a power source that cannot simply deliver more:** a gas
genset burning flare gas at a wellhead, a stranded generator, or a grid
connection with curtailment terms. It is a deterministic controller — no model
in the loop — with a hard failsafe, real hardware drivers, a per-site
economics engine, and a live dashboard. MIT licensed.

```
 genset / PLC ──Modbus──▶ ┌──────────────────────┐ ──CGMiner API──▶ miner 1 (BOS)
   kW · Hz · alarms       │  controller (10 s)   │                  miner 2 (LuxOS)
                          │  smooth → buffer →   │                  …
                          │  hysteresis band →   │ ◀── hashrate ─── miner N
                          │  frequency governor  │
                          │  failsafe → flare    │ ──REST/WS──▶ dashboard · ROI
                          └──────────────────────┘
```

## What it does

* **Tracks available power.** Reads generator output every loop interval,
  smooths it over a short window, applies a buffer factor (default 90 %), and
  adds or sheds **one miner per tick** inside a hysteresis dead-band so the
  fleet follows the gas without flapping. Shedding is by priority.
* **Protects the engine on frequency, not just kW.** An islanded genset sags
  in Hz before any kW figure shows it is overloaded. Below nominal − 0.3 Hz
  no miner is added; below nominal − 1.0 Hz one miner is shed per tick
  regardless of headroom; below nominal − 2.5 Hz everything trips. Margins
  and nominal (50/60 Hz) are configuration.
* **Fails safe.** A generator fault or alarm word, a dead Modbus link, or a
  frequency collapse turns every miner off in one tick; the gas goes back to
  the flare, which is where it was going anyway.
* **Drives real hardware.**
  * Genset side: Modbus TCP via a `RegisterMap` — kW (16/32-bit, signed),
    bus frequency, alarm word. Presets for the single-register "generic"
    case and for DSE GenComm; ComAp via overrides. Presets that have not
    been verified on hardware say so at start-up.
  * Miner side: the CGMiner-family API on port 4028. `pause`/`resume` on
    Braiins OS, `logon` + `curtail` on LuxOS, read-only on stock Bitmain
    firmware (switch those with a smart PDU). A resumed miner is counted as
    committed load while it boots; one that never hashes is flagged.
* **Prices the site before you buy iron.** `economics.py` is a pure, tested
  engine for a dual-workload pad — firmed AI/GPU baseload plus interruptible
  Bitcoin — returning NPV, IRR, payback, CO₂e avoided and the uplift the
  controller adds over a static build. Every default cites its source.
  Region presets: Permian, Bakken, Vaca Muerta, Oman, and a pessimist case.
* **Shows it.** A single-file dashboard (served at `/`) with a live
  power-vs-load chart, the fleet table with per-miner control, scenario
  injection (ramp, sudden drop, noisy gas, under-frequency), an 82-second
  scripted demo, and the ROI panel with API-driven sliders and a
  print-to-PDF leave-behind.

## What it is not (yet)

* It has run against **simulated** hardware and mocked Modbus/CGMiner
  endpoints. The drivers are unit-tested against canned replies; they have
  not yet been proven on a wellhead. The `dse_gencomm` register offsets are
  taken from the GenComm listing and are marked unverified until someone
  confirms them on a panel.
* No authentication on the API. Put it behind a VPN or a reverse proxy.
* Single controller per process by design (`--workers 1`); it is a site
  controller, not a fleet-of-sites platform.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
cp .env.example .env                                                 # optional
./run.sh          # or: make dev   |   ./run.ps1 on Windows
```

Open <http://localhost:8000> for the dashboard, `/docs` for the API. Click
**Run Investor Demo** for the scripted story: steady → gas rising → sudden
drop → recovery → generator fault → recovery.

### Live mode

```ini
MODE=LIVE
PLC_HOST=10.0.0.10   PLC_PORT=502   PLC_UNIT_ID=1
GENSET_MAP=dse_gencomm         # or generic + PLC_KW_REGISTER, or overrides
NOMINAL_HZ=60.0                # Americas
FLEET_BACKEND=CGMINER
FLEET_CONFIG=fleet.json        # copy fleet.example.json and edit
```

## Test

```bash
make test        # pytest -q — 89 tests, no network, no hardware
flake8 app tests && pydocstyle app
python scripts/verify_live.py https://your-deployment   # behavioural check against a running instance
```

## Layout

```
app/
  main.py                 FastAPI app, control loop lifecycle, dashboard
  config.py               settings (.env) — mode, Modbus, governor, fleet
  models.py / schemas.py  domain models and the camelCase wire contract
  routers/                plc · miners · controller · logs · scenario · demo · economics · ws
  services/
    controller.py         the loop: smoothing, hysteresis, frequency governor, failsafe, demo
    plc.py                PlcInterface, SimulatedPlc, single-register RealPlc
    genset.py             ModbusGenset + RegisterMap presets (kW · Hz · alarms)
    miners.py             simulated fleet
    fleet.py              real fleet over the CGMiner/BOSminer/LuxOS API
    economics.py          dual-workload ROI engine
  static/index.html       dashboard
tests/                    89 tests: controller, governor, PLC, genset, fleet, economics, API, deploy config
scripts/verify_live.py    single-controller and demo checks against a live URL
```

## API

`GET /api/plc/latest` · `GET /api/miners` · `POST /api/miners/{id}/power` ·
`GET /api/controller/state` · `PUT /api/controller/config` · `GET /api/logs` ·
`POST /api/scenario` · `POST /api/demo/run` · `POST /api/demo/failsafe` · `GET /api/roi/defaults` ·
`POST /api/roi/calculate` · `WS /ws` (snapshots every tick). Full schema at
`/docs`.

## Deploy

`Dockerfile` + `render.yaml` are set for a one-click Render deploy; see
`DEPLOY.md`. WebSockets need a paid instance (the free tier sleeps).

## Roadmap

1. Replay of public flare-gas datasets (NDIC monthly flaring, VIIRS) so the
   ROI numbers are anchored to a real pad's profile rather than a synthetic ramp.
2. Hardware verification of the DSE and ComAp register maps; a Bitaxe on a
   bench genset as the smallest honest field test.
3. Curtailment-signal input (grid operator / aggregator API) so the same
   governor runs a grid-connected site under an interruptible connection.
4. Multi-site: one controller per site, one view across sites.

## Author

Ali Emadi — process engineer (Aker Solutions, FEED on gas processing; Baker
Hughes, real-time North Sea operations) turned applied AI engineer. Oslo.
