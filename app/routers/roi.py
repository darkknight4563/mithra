"""ROI calculator API.

Serves the authoritative defaults + slider metadata (so the frontend never
hard-codes constants) and runs the dual-workload model from app.services.roi.
All economics trace to the techno-economic research report (June 2026).
"""

from fastapi import APIRouter

from app.services import roi

router = APIRouter(prefix="/api/roi", tags=["roi"])

# Slider metadata: ranges/units/citations from the report. "tier" 1 = the three
# dominant axes (report Recommendation 1); 2 = secondary; 3 = site/hardware.
SLIDERS = [
    {"key": "btcPrice", "label": "BTC Price", "min": 40000, "max": 130000, "step": 500,
     "unit": "$", "tier": 1, "cite": "§1 — $64,227 CoinDesk 21 Jun 2026"},
    {"key": "hashpricePerThDay", "label": "Hashprice", "min": 0.02, "max": 0.10, "step": 0.001,
     "unit": "$/TH/day", "tier": 1, "cite": "§1 — ~$35-38/PH/day"},
    {"key": "gpuRentalPerHr", "label": "GPU Rental (H100-class)", "min": 1.0, "max": 6.0,
     "step": 0.05, "unit": "$/GPU-hr", "tier": 1, "cite": "§2 — H100 ~$2.43-2.63/hr"},
    {"key": "captureFraction", "label": "Remote AI Capture", "min": 0.40, "max": 0.85,
     "step": 0.01, "unit": "fraction", "tier": 2, "cite": "§2 — modeled 50-75%"},
    {"key": "gasPricePerMcf", "label": "Gas Price (stranded)", "min": -1.0, "max": 5.0,
     "step": 0.1, "unit": "$/Mcf", "tier": 2, "cite": "§3 — ~$0; negative if penalty"},
    {"key": "gasFlowMcfPerDay", "label": "Gas Flow", "min": 200, "max": 20000, "step": 100,
     "unit": "Mcf/day", "tier": 3, "cite": "§4 — 1 MW-class pad default 2000"},
    {"key": "kwhPerMcf", "label": "Electricity Yield", "min": 8, "max": 14, "step": 0.5,
     "unit": "kWh/Mcf", "tier": 3, "cite": "§3 — 11 @35-40% genset eff"},
    {"key": "uptime", "label": "Uptime", "min": 0.85, "max": 0.97, "step": 0.01,
     "unit": "fraction", "tier": 3, "cite": "§1 — remote oilfield 90-95%"},
    {"key": "aiAllocation", "label": "AI Share of Firm kW", "min": 0.0, "max": 1.0, "step": 0.05,
     "unit": "fraction", "tier": 2, "cite": "§4 — AI baseload, BTC the remainder"},
    {"key": "gensetCapexPerKw", "label": "Genset Capex", "min": 1000, "max": 2500, "step": 50,
     "unit": "$/kW", "tier": 3, "cite": "§3 — recip ~$1,500/kW"},
    {"key": "discountRate", "label": "Discount Rate", "min": 0.05, "max": 0.25, "step": 0.01,
     "unit": "fraction", "tier": 3, "cite": "§4 — 12%"},
    {"key": "projectYears", "label": "Project Life", "min": 2, "max": 8, "step": 1,
     "unit": "years", "tier": 3, "cite": "§4 — 4-year life"},
]


@router.get("/defaults")
async def defaults() -> dict:
    """Return default input values + slider metadata (single source of truth)."""
    return {"values": roi.RoiInputs().model_dump(by_alias=True), "sliders": SLIDERS}


@router.post("/calculate")
async def calculate(inputs: roi.RoiInputs) -> dict:
    """Compute the full dual-workload ROI result for the given inputs."""
    return roi.compute(inputs)


@router.post("/sensitivity")
async def sensitivity(inputs: roi.RoiInputs, metric: str = "npv") -> dict:
    """Return tornado-chart sensitivity for the dominant axes (report §4)."""
    return roi.sensitivity(inputs, metric)
