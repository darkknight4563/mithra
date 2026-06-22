"""Dual-workload flare-gas ROI engine (per-site sales tool).

Pure, audit-friendly economics: a stranded/flare-gas generator powers BOTH
interruptible Bitcoin ASICs and always-on AI/HPC GPUs. AI takes the firm power
floor (higher value); Bitcoin absorbs the variable top (interruptible). The
"with vs without" uplift this produces is the justification for the software fee.

Every constant lives in ECONOMIC_DEFAULTS with an inline citation to the
techno-economic research report ("Gatekeeper AI: Techno-Economic Inputs…",
June 2026). Functions are pure (no I/O) and individually unit-testable.
"""

from __future__ import annotations

from typing import Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# World Bank 2025 Global Gas Flaring Tracker: 2024 flaring 151 bcm -> 389 Mt
# CO2e. 389e6 t / 151e9 m3 = 2.58e-3 t/m3; 1 Mcf = 28.317 m3 -> ~0.073 tCO2e
# per Mcf flared (report §3 Emissions/ESG, World Bank 18 Jul 2025).
CO2E_TONNES_PER_MCF_FLARED = 0.073

# ---------------------------------------------------------------------------
# Single source of truth for every economic assumption. Each value is the
# report's number; the comment cites the figure, source and date.
# ---------------------------------------------------------------------------
ECONOMIC_DEFAULTS = {
    # ---- site / gas input ----
    "gas_mcf_per_day": 2000.0,        # 1 MW-class pad; report §4 default (range 200-20000)
    "kwh_per_mcf": 11.0,              # elec yield @~35-40% genset eff; report §3 (range 8-14)
    "gas_cost_usd_per_mcf": 0.0,      # stranded/flared ~$0; report §3 (negative if penalty)
    "gas_variability": 0.15,          # non-firm fraction BTC absorbs; modeled, report §1 uptime

    # ---- Bitcoin economics (report §1, June 2026) ----
    "btc_price_usd": 64000.0,         # CoinDesk $64,227, 21 Jun 2026 (context; in hashprice)
    "hashprice_usd_per_ph_day": 36.0,  # ~$35-38/PH/day; report §1 (May 2026); range 20-100
    "miner_efficiency_j_per_th": 16.0,  # S21-class (base 17.5, Pro 15); report §1 ASIC table
    "miner_kw": 3.5,                  # Antminer S21 ~3.5 kW; report §1
    "miner_th": 220.0,                # ~200-234 TH/s S21-class; report §1
    "miner_price_usd": 3520.0,        # ~$16/TH x 220 TH; report §1 (S21 Pro $3,000-3,800)

    # ---- AI / HPC economics (report §2, 2026) ----
    "gpu_rental_usd_per_hour": 2.50,  # H100-class neocloud ~$2.43-2.63/hr; report §2 (1-6)
    "gpu_kw": 1.3,                    # per H100-class GPU incl overhead+cooling; report §2
    "gpu_price_usd": 30000.0,         # H100-class ~$25-30k; report §2
    "gpu_fleet_kw": 10000.0,          # installed GPU cap (kW); default high = size to firm
    "remote_ai_capture_fraction": 0.60,  # remote capture vs Tier-3; report §2 (50-75%)
    "ai_baseload_target": 0.50,       # share of FIRM kW for always-on AI; report §4

    # ---- gas-to-power capex/opex (report §3) ----
    "genset_capex_usd_per_kw": 1500.0,  # recip engine; report §3 Thunder Said (1000-2500)
    "genset_om_usd_per_kwh": 0.02,    # recip O&M $15-25/MWh; report §3
    "labor_per_day": 150.0,           # USD/day per site; report §4 model estimate

    # ---- uptime (report §1: remote oilfield 90-95%; AI firmed runs higher) ----
    "btc_uptime": 0.95,               # interruptible miners on variable power; report §1
    "ai_uptime": 0.98,                # AI on firm/battery-backed power; report §1/§2

    # ---- ESG (report §3) ----
    "co2e_reduction_fraction": 0.63,  # vs open flaring; report §3 (Crusoe, directional)

    # ---- finance (report §4) ----
    "discount_rate": 0.12,            # report §4
    "project_life_years": 4,          # report §4

    # ---- software business model (report §5) ----
    "software_fee_mode": "per_mw_month",      # "per_mw_month" | "revenue_share"
    "software_fee_per_mw_month": 200.0,       # $/MW/mo; report §5 ($100-300; Luxor $100 benchmark)
    "software_revenue_share_pct": 0.02,       # 2% of gross compute revenue; report §5 (1-3%)
}


# Per-slider UI metadata + validation ranges + the runtime citation string.
# The engine owns this so the frontend builds sliders from one place. Keys are
# snake_case (match ECONOMIC_DEFAULTS); the API emits them camelCase.
FIELD_META = {
    "gas_mcf_per_day": {
        "label": "Gas Flow", "min": 0, "max": 20000, "step": 100,
        "unit": "Mcf/day", "cite": "report §4 — 1 MW pad ~2,000 Mcf/day",
    },
    "gas_cost_usd_per_mcf": {
        "label": "Gas Cost", "min": -1.0, "max": 5.0, "step": 0.1,
        "unit": "$/Mcf", "cite": "report §3 — stranded ~$0; negative if penalty",
    },
    "gas_variability": {
        "label": "Gas Variability", "min": 0.0, "max": 0.4, "step": 0.01,
        "unit": "fraction", "cite": "report §1 — remote uptime 90-95%",
    },
    "hashprice_usd_per_ph_day": {
        "label": "Hashprice", "min": 20, "max": 100, "step": 1,
        "unit": "$/PH/day", "cite": "report §1 — ~$35-38/PH/day (Jun 2026)",
    },
    "miner_efficiency_j_per_th": {
        "label": "Miner Efficiency", "min": 9.5, "max": 25, "step": 0.5,
        "unit": "J/TH", "cite": "report §1 — S21-class 15-17.5 J/TH",
    },
    "gpu_rental_usd_per_hour": {
        "label": "GPU Rental", "min": 1.0, "max": 6.0, "step": 0.05,
        "unit": "$/GPU-hr", "cite": "report §2 — H100 ~$2.43-2.63/hr",
    },
    "remote_ai_capture_fraction": {
        "label": "Remote AI Capture", "min": 0.40, "max": 0.85, "step": 0.01,
        "unit": "fraction", "cite": "report §2 — modeled 50-75%",
    },
    "ai_baseload_target": {
        "label": "AI Share of Firm kW", "min": 0.0, "max": 1.0, "step": 0.05,
        "unit": "fraction", "cite": "report §4 — AI baseload, BTC the rest",
    },
    "kwh_per_mcf": {
        "label": "Electricity Yield", "min": 8, "max": 14, "step": 0.5,
        "unit": "kWh/Mcf", "cite": "report §3 — 11 @35-40% genset eff",
    },
    "genset_capex_usd_per_kw": {
        "label": "Genset Capex", "min": 1000, "max": 2500, "step": 50,
        "unit": "$/kW", "cite": "report §3 — recip ~$1,500/kW",
    },
    "btc_uptime": {
        "label": "BTC Uptime", "min": 0.85, "max": 0.99, "step": 0.01,
        "unit": "fraction", "cite": "report §1 — interruptible 90-95%",
    },
    "ai_uptime": {
        "label": "AI Uptime", "min": 0.90, "max": 0.999, "step": 0.005,
        "unit": "fraction", "cite": "report §1/§2 — firmed power",
    },
    "discount_rate": {
        "label": "Discount Rate", "min": 0.05, "max": 0.25, "step": 0.01,
        "unit": "fraction", "cite": "report §4 — 12%",
    },
    "project_life_years": {
        "label": "Project Life", "min": 2, "max": 8, "step": 1,
        "unit": "years", "cite": "report §4 — 4-year life",
    },
    "software_fee_per_mw_month": {
        "label": "Software Fee", "min": 0, "max": 500, "step": 10,
        "unit": "$/MW/mo", "cite": "report §5 — $100-300/MW/mo",
    },
    "software_revenue_share_pct": {
        "label": "Revenue Share", "min": 0.0, "max": 0.05, "step": 0.005,
        "unit": "fraction", "cite": "report §5 — 1-3% of gross",
    },
}

# Region presets — override maps from the report's regional notes (§6).
PRESETS = [
    {
        "id": "permian", "label": "Permian (TX)",
        "cite": "report §6 — primary US market; Texas miner-friendly",
        "overrides": {
            "gas_mcf_per_day": 3000, "gas_cost_usd_per_mcf": 0.5,
            "remote_ai_capture_fraction": 0.65, "btc_uptime": 0.95,
        },
    },
    {
        "id": "bakken", "label": "Bakken (ND)",
        "cite": "report §6 — remote, cold; stranded gas, lower connectivity",
        "overrides": {
            "gas_mcf_per_day": 2500, "gas_cost_usd_per_mcf": 0.0,
            "remote_ai_capture_fraction": 0.55, "btc_uptime": 0.92,
        },
    },
    {
        "id": "vaca_muerta", "label": "Vaca Muerta (AR)",
        "cite": "report §6 — midstream bottlenecks force flaring (negative gas)",
        "overrides": {
            "gas_mcf_per_day": 4000, "gas_cost_usd_per_mcf": -0.5,
            "remote_ai_capture_fraction": 0.50, "gas_variability": 0.20,
        },
    },
    {
        "id": "oman_uae", "label": "Oman/UAE",
        "cite": "report §6 — large flaring, capital available, zero-flare goals",
        "overrides": {
            "gas_mcf_per_day": 5000, "gas_cost_usd_per_mcf": 0.0,
            "remote_ai_capture_fraction": 0.60, "btc_uptime": 0.96,
        },
    },
]


def _slider(key: str):
    """Build a constrained pydantic Field for a slider input from FIELD_META."""
    meta = FIELD_META[key]
    return Field(ECONOMIC_DEFAULTS[key], ge=meta["min"], le=meta["max"])


# ===========================================================================
# Pure functions (no I/O) — each independently auditable & testable.
# ===========================================================================

def gas_to_kw(
    mcf_per_day: float,
    kwh_per_mcf: float = ECONOMIC_DEFAULTS["kwh_per_mcf"],
) -> float:
    """Convert daily gas flow (Mcf/day) to average available electrical kW.

    available_kw = mcf_per_day * kwh_per_mcf / 24  (report §3 yield).
    """
    return mcf_per_day * kwh_per_mcf / 24.0


def allocate_dual(
    available_kw: float,
    ai_baseload_target: float,
    gas_variability: float,
    gpu_fleet_kw: float,
) -> Tuple[float, float]:
    """Split available power between AI (firm baseload) and Bitcoin (variable top).

    The arbitrage thesis (report Key Findings 1-2):
        firm_kw = available_kw * (1 - gas_variability)   # reliable power floor
        ai_kw   = min(gpu_fleet_kw, firm_kw * ai_baseload_target)  # AI on firm only
        btc_kw  = available_kw - ai_kw                    # BTC absorbs the rest

    Returns (ai_kw, btc_kw); btc_kw is floored at 0 for robustness.
    """
    firm_kw = available_kw * (1.0 - gas_variability)
    ai_kw = min(gpu_fleet_kw, firm_kw * ai_baseload_target)
    ai_kw = min(ai_kw, available_kw)
    btc_kw = max(0.0, available_kw - ai_kw)
    return ai_kw, btc_kw


def btc_revenue_per_day(
    btc_kw: float,
    miner_efficiency_j_per_th: float = ECONOMIC_DEFAULTS["miner_efficiency_j_per_th"],
    hashprice_usd_per_ph_day: float = ECONOMIC_DEFAULTS["hashprice_usd_per_ph_day"],
    uptime: float = ECONOMIC_DEFAULTS["btc_uptime"],
) -> Tuple[float, float]:
    """Daily Bitcoin revenue and the power-limited hashrate (TH/s).

    Power limits hashrate: W = (J/TH) * (TH/s) -> th_s = btc_kw*1000 / J_per_TH.
    Hashprice is $/PH/day, so revenue = (th_s/1000) * hashprice * uptime
    (report §1; hashprice already bundles subsidy+fees/difficulty).
    Returns (usd_per_day, th_per_second).
    """
    if miner_efficiency_j_per_th <= 0:
        return 0.0, 0.0
    th_s = btc_kw * 1000.0 / miner_efficiency_j_per_th
    usd_day = (th_s / 1000.0) * hashprice_usd_per_ph_day * uptime
    return usd_day, th_s


def ai_revenue_per_day(
    ai_kw: float,
    gpu_kw: float = ECONOMIC_DEFAULTS["gpu_kw"],
    gpu_rental_rate: float = ECONOMIC_DEFAULTS["gpu_rental_usd_per_hour"],
    capture_fraction: float = ECONOMIC_DEFAULTS["remote_ai_capture_fraction"],
    uptime: float = ECONOMIC_DEFAULTS["ai_uptime"],
) -> Tuple[float, int]:
    """Daily AI/GPU rental revenue and the integer GPU count that fits ai_kw.

    revenue = gpu_count * rate * 24 * capture_fraction * uptime (report §2).
    capture_fraction haircuts remote-site rental vs Tier-3 datacenter.
    Returns (usd_per_day, gpu_count).
    """
    if gpu_kw <= 0:
        return 0.0, 0
    gpu_count = int(ai_kw // gpu_kw)
    usd_day = gpu_count * gpu_rental_rate * 24.0 * capture_fraction * uptime
    return usd_day, gpu_count


def daily_opex(
    mcf_per_day: float,
    gas_cost: float,
    available_kw: float,
    genset_om: float = ECONOMIC_DEFAULTS["genset_om_usd_per_kwh"],
    labor_per_day: float = ECONOMIC_DEFAULTS["labor_per_day"],
    software_fee_per_day: float = 0.0,
) -> float:
    """Daily operating cost: fuel + genset O&M + labor + software fee (report §3-5).

    Fuel can be negative when the operator is paid to consume flared gas.
    """
    fuel_cost = mcf_per_day * gas_cost
    genset_om_cost = available_kw * 24.0 * genset_om
    return fuel_cost + genset_om_cost + labor_per_day + software_fee_per_day


def site_capex(
    miner_count: int,
    miner_price: float,
    gpu_count: int,
    gpu_price: float,
    genset_kw: float,
    genset_capex_per_kw: float = ECONOMIC_DEFAULTS["genset_capex_usd_per_kw"],
) -> float:
    """Total upfront capex: miners + GPUs + generator (report §1-3)."""
    return (
        miner_count * miner_price
        + gpu_count * gpu_price
        + genset_kw * genset_capex_per_kw
    )


def payback_months(capex: float, daily_net: float) -> Optional[float]:
    """Simple payback in months, or None if the site never pays back (net<=0)."""
    if daily_net <= 0:
        return None
    return capex / (daily_net * 30.0)


def npv(rate: float, cashflows: list[float]) -> float:
    """Net present value: sum cf_t / (1+rate)^t, t=0..n (t0 = capex outflow)."""
    return sum(cf / (1.0 + rate) ** t for t, cf in enumerate(cashflows))


def irr(cashflows: list[float], tol: float = 1e-7, max_iter: int = 200) -> Optional[float]:
    """Internal rate of return via bisection on NPV.

    Brackets the root in (-0.9999, 10.0). Returns None when there is no sign
    change (e.g. an all-negative cashflow series), so callers never crash.
    """
    low, high = -0.9999, 10.0
    f_low = npv(low, cashflows)
    f_high = npv(high, cashflows)
    if f_low == 0:
        return low
    if f_high == 0:
        return high
    if f_low * f_high > 0:  # no sign change -> no real IRR in range
        return None
    for _ in range(max_iter):
        mid = (low + high) / 2.0
        f_mid = npv(mid, cashflows)
        if abs(f_mid) < tol or (high - low) / 2.0 < tol:
            return mid
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2.0


def co2e_tonnes_avoided_per_year(
    mcf_per_day: float,
    co2e_reduction_fraction: float = ECONOMIC_DEFAULTS["co2e_reduction_fraction"],
) -> float:
    """Annual CO2e tonnes avoided vs open flaring (report §3).

    = mcf/day * 365 * tCO2e-per-Mcf-flared * reduction_fraction.
    """
    return (
        mcf_per_day
        * 365.0
        * CO2E_TONNES_PER_MCF_FLARED
        * co2e_reduction_fraction
    )


# ===========================================================================
# Pydantic I/O models
# ===========================================================================

class RoiInputs(BaseModel):
    """All inputs, defaulted from ECONOMIC_DEFAULTS; slider fields are range-validated.

    Accepts camelCase JSON in (and serializes camelCase out) via aliasing;
    out-of-range slider values raise a 422 at the API boundary.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # slider fields (range-validated from FIELD_META)
    gas_mcf_per_day: float = _slider("gas_mcf_per_day")
    kwh_per_mcf: float = _slider("kwh_per_mcf")
    gas_cost_usd_per_mcf: float = _slider("gas_cost_usd_per_mcf")
    gas_variability: float = _slider("gas_variability")
    hashprice_usd_per_ph_day: float = _slider("hashprice_usd_per_ph_day")
    miner_efficiency_j_per_th: float = _slider("miner_efficiency_j_per_th")
    gpu_rental_usd_per_hour: float = _slider("gpu_rental_usd_per_hour")
    remote_ai_capture_fraction: float = _slider("remote_ai_capture_fraction")
    ai_baseload_target: float = _slider("ai_baseload_target")
    genset_capex_usd_per_kw: float = _slider("genset_capex_usd_per_kw")
    btc_uptime: float = _slider("btc_uptime")
    ai_uptime: float = _slider("ai_uptime")
    discount_rate: float = _slider("discount_rate")
    project_life_years: int = _slider("project_life_years")
    software_fee_per_mw_month: float = _slider("software_fee_per_mw_month")
    software_revenue_share_pct: float = _slider("software_revenue_share_pct")

    # non-slider fields (sensible defaults; still overridable)
    btc_price_usd: float = ECONOMIC_DEFAULTS["btc_price_usd"]
    miner_kw: float = Field(ECONOMIC_DEFAULTS["miner_kw"], gt=0)
    miner_th: float = ECONOMIC_DEFAULTS["miner_th"]
    miner_price_usd: float = Field(ECONOMIC_DEFAULTS["miner_price_usd"], ge=0)
    gpu_kw: float = Field(ECONOMIC_DEFAULTS["gpu_kw"], gt=0)
    gpu_price_usd: float = Field(ECONOMIC_DEFAULTS["gpu_price_usd"], ge=0)
    gpu_fleet_kw: float = Field(ECONOMIC_DEFAULTS["gpu_fleet_kw"], ge=0)
    genset_om_usd_per_kwh: float = Field(ECONOMIC_DEFAULTS["genset_om_usd_per_kwh"], ge=0)
    labor_per_day: float = Field(ECONOMIC_DEFAULTS["labor_per_day"], ge=0)
    co2e_reduction_fraction: float = Field(
        ECONOMIC_DEFAULTS["co2e_reduction_fraction"], ge=0, le=1
    )
    software_fee_mode: Literal["per_mw_month", "revenue_share"] = (
        ECONOMIC_DEFAULTS["software_fee_mode"]
    )


class RoiResult(BaseModel):
    """Everything the dashboard shows for one site scenario (camelCase JSON out)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # power build-out
    available_kw: float
    firm_kw: float
    ai_kw: float
    btc_kw: float
    gpu_count: int
    miner_count: int
    btc_hashrate_th_s: float = Field(..., description="power-limited TH/s")

    # revenue (daily) split by workload
    btc_revenue_day: float
    ai_revenue_day: float
    gross_revenue_day: float

    # cost (daily)
    fuel_cost_day: float
    opex_day: float
    software_fee_day: float
    software_fee_monthly: float

    # net at three cadences
    daily_net: float
    monthly_net: float
    annual_net: float

    # capital & returns
    capex: float
    payback_months: Optional[float]
    irr: Optional[float]
    npv: float

    # ESG (explicit alias: to_camel would otherwise capitalise the E after "2")
    co2e_tonnes_per_year: float = Field(alias="co2eTonnesPerYear")

    # with-vs-without (the software-fee justification)
    without_daily_net: float
    with_daily_net: float
    uplift_daily: float
    uplift_pct: Optional[float]


def _software_fee(
    inp: RoiInputs, available_kw: float, gross_revenue_day: float
) -> Tuple[float, float]:
    """Return (fee_per_day, fee_per_month) for the configured fee mode (§5)."""
    if inp.software_fee_mode == "revenue_share":
        fee_day = gross_revenue_day * inp.software_revenue_share_pct
        return fee_day, fee_day * 30.0
    site_mw = available_kw / 1000.0
    fee_month = site_mw * inp.software_fee_per_mw_month
    return fee_month / 30.0, fee_month


def evaluate(inp: RoiInputs) -> RoiResult:
    """Run the full dual-workload model and the with-vs-without comparison."""
    available_kw = gas_to_kw(inp.gas_mcf_per_day, inp.kwh_per_mcf)
    firm_kw = available_kw * (1.0 - inp.gas_variability)
    ai_kw, btc_kw = allocate_dual(
        available_kw, inp.ai_baseload_target, inp.gas_variability, inp.gpu_fleet_kw
    )

    btc_rev, btc_th_s = btc_revenue_per_day(
        btc_kw, inp.miner_efficiency_j_per_th, inp.hashprice_usd_per_ph_day, inp.btc_uptime
    )
    ai_rev, gpu_count = ai_revenue_per_day(
        ai_kw, inp.gpu_kw, inp.gpu_rental_usd_per_hour,
        inp.remote_ai_capture_fraction, inp.ai_uptime,
    )
    gross_rev = btc_rev + ai_rev
    miner_count = int(btc_kw // inp.miner_kw)

    fee_day, fee_month = _software_fee(inp, available_kw, gross_rev)
    opex = daily_opex(
        inp.gas_mcf_per_day, inp.gas_cost_usd_per_mcf, available_kw,
        inp.genset_om_usd_per_kwh, inp.labor_per_day, fee_day,
    )
    daily_net = gross_rev - opex

    capex = site_capex(
        miner_count, inp.miner_price_usd, gpu_count, inp.gpu_price_usd,
        available_kw, inp.genset_capex_usd_per_kw,
    )
    annual_net = daily_net * 365.0
    cashflows = [-capex] + [annual_net] * inp.project_life_years

    # "without": naive Bitcoin-only on ALL available power, static, no software fee.
    w0_rev, _ = btc_revenue_per_day(
        available_kw, inp.miner_efficiency_j_per_th,
        inp.hashprice_usd_per_ph_day, inp.btc_uptime,
    )
    w0_opex = daily_opex(
        inp.gas_mcf_per_day, inp.gas_cost_usd_per_mcf, available_kw,
        inp.genset_om_usd_per_kwh, inp.labor_per_day, 0.0,
    )
    without_net = w0_rev - w0_opex
    uplift = daily_net - without_net
    uplift_pct = (uplift / without_net) if without_net > 0 else None

    return RoiResult(
        available_kw=available_kw,
        firm_kw=firm_kw,
        ai_kw=ai_kw,
        btc_kw=btc_kw,
        gpu_count=gpu_count,
        miner_count=miner_count,
        btc_hashrate_th_s=btc_th_s,
        btc_revenue_day=btc_rev,
        ai_revenue_day=ai_rev,
        gross_revenue_day=gross_rev,
        fuel_cost_day=inp.gas_mcf_per_day * inp.gas_cost_usd_per_mcf,
        opex_day=opex,
        software_fee_day=fee_day,
        software_fee_monthly=fee_month,
        daily_net=daily_net,
        monthly_net=daily_net * 30.0,
        annual_net=annual_net,
        capex=capex,
        payback_months=payback_months(capex, daily_net),
        irr=irr(cashflows),
        npv=npv(inp.discount_rate, cashflows),
        co2e_tonnes_per_year=co2e_tonnes_avoided_per_year(
            inp.gas_mcf_per_day, inp.co2e_reduction_fraction
        ),
        without_daily_net=without_net,
        with_daily_net=daily_net,
        uplift_daily=uplift,
        uplift_pct=uplift_pct,
    )
