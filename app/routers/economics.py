"""ROI API — exposes the economics engine as the single source of truth.

The dashboard posts slider values to /api/roi/calculate on every change and
builds its sliders from /api/roi/defaults, so defaults, ranges and citations
live only in app.services.economics (never duplicated in the frontend).
"""

from fastapi import APIRouter
from pydantic.alias_generators import to_camel

from app.services import economics
from app.services.economics import RoiInputs, RoiResult

router = APIRouter(prefix="/api/roi", tags=["roi"])


@router.post("/calculate", response_model=RoiResult)
async def calculate(inputs: RoiInputs) -> RoiResult:
    """Run the dual-workload model. Pure arithmetic — safe to call per slider move.

    Bad/out-of-range inputs are rejected with a 422 by RoiInputs validation.
    """
    return economics.evaluate(inputs)


@router.get("/defaults")
async def defaults() -> dict:
    """Return per-slider metadata (default, range, step, unit, citation) + presets.

    The frontend builds its sliders entirely from this response.
    """
    base = RoiInputs()
    fields = [
        {
            "key": to_camel(key),
            "group": meta["group"],
            "value": getattr(base, key),
            "min": meta["min"],
            "max": meta["max"],
            "step": meta["step"],
            "unit": meta["unit"],
            "label": meta["label"],
            "cite": meta["cite"],
        }
        for key, meta in economics.FIELD_META.items()
    ]
    presets = [
        {
            "id": p["id"],
            "label": p["label"],
            "cite": p["cite"],
            "overrides": {to_camel(k): v for k, v in p["overrides"].items()},
        }
        for p in economics.PRESETS
    ]
    return {"fields": fields, "presets": presets}
