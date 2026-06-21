# Gatekeeper AI / Flare Control System — Backend

FastAPI backend for the flare-gas Bitcoin mining control dashboard.

This is the **runnable foundation** only — no domain features yet. It boots a
FastAPI app, applies CORS for the React (Lovable) dashboard, and exposes a
single `GET /health` endpoint.

## Stack

Python 3.11+ · FastAPI · uvicorn · pydantic-settings · pymodbus · websockets · pytest

## Layout

```
app/
  main.py        # FastAPI app + /health
  config.py      # settings via pydantic-settings (.env)
  models.py      # shared pydantic models
  routers/       # (empty — feature routers go here)
  services/      # (empty — business logic / PLC + sim services go here)
tests/
  test_health.py
.env.example
requirements.txt
```

## Setup

```bash
python -m venv .venv
# Windows:  .venv\Scripts\pip install -r requirements.txt
# Unix:     .venv/bin/pip install -r requirements.txt
```

Optionally copy `.env.example` to `.env` and adjust.

## Run

```bash
./run.sh                 # macOS / Linux / Git Bash
# or
make dev                 # if make is installed
# or (Windows PowerShell):
./run.ps1
```

The server listens on `http://0.0.0.0:8000` with auto-reload.

## Verify

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

Interactive docs: http://localhost:8000/docs

## Test

```bash
make test
# or
.venv/bin/pytest -q       # .venv\Scripts\pytest -q on Windows
```
