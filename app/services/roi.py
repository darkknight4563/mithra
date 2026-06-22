"""Dual-workload flare-gas compute ROI model.

Authoritative source of every default constant and formula: the techno-economic
research report "Gatekeeper AI: Techno-Economic Inputs for a Dual-Workload
Flare-Gas Compute ROI Calculator" (June 2026). Each default below cites the
report section it comes from. Figures are June-2026 snapshots and meant to be
overridden by the user; treat BTC price, hashprice and GPU rental as the three
dominant sensitivity axes (report §4 formula notes).

Thesis (report TL;DR / Key Findings 1-2): stranded-gas gensets feed BOTH
interruptible Bitcoin ASICs (the low-value, infinitely-curtailable "power
sponge") and firmed AI/GPU compute (the higher-value baseload). AI gets the
firm kW; Bitcoin absorbs the remainder. This dual-workload arbitrage IS the
product, and the defensible solo play is the orchestration SOFTWARE, not
operating capital-intensive hardware (Key Finding 4 / §6).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

# --- constants the user does not normally tune (report §3 ESG) ---------------
# World Bank 2025 Global Gas Flaring Tracker: 2024 flaring 151 bcm emitted
# 389 Mt CO2e -> 389e6 t / 151e9 m3 = 2.58e-3 t/m3; 1 Mcf = 28.317 m3 ->
# ~0.073 tCO2e per Mcf flared. Crusoe-cited ~63% CO2e reduction vs flaring when
# burned in an enclosed engine -> ~0.046 tCO2e avoided per Mcf consumed.
CO2E_TONNES_PER_MCF_FLARED = 0.073
CO2E_REDUCTION_VS_FLARING = 0.63  # report §3 (Crusoe, self-reported/directional)
CO2E_AVOIDED_PER_MCF = CO2E_TONNES_PER_MCF_FLARED * CO2E_REDUCTION_VS_FLARING

LABOR_HOSTING_USD_PER_DAY = 150.0  # report §4 model estimate ("USD/day per site")


class RoiInputs(BaseModel):
    """All calculator inputs with defensible June-2026 defaults (report §4)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # ---- the three dominant sensitivity axes (report Recommendation 1) ----
    btc_price: float = 64000.0          # USD; report §1 (CoinDesk $64,227). Range 40k-130k
    hashprice_per_th_day: float = 0.036  # USD/TH/day ($35-38/PH/day); report §1. Range 0.02-0.10
    gpu_rental_per_hr: float = 2.50      # USD/GPU-hr, H100-class Tier-3 ref; report §2. Range 1-6

    # ---- secondary tier (report Recommendation 1) ----
    capture_fraction: float = 0.65       # remote AI capture; report §2 synthesis (0.40-0.85)
    gas_price_per_mcf: float = 0.50      # USD/Mcf, can be negative; report §3 (-1 to 5)

    # ---- site / gas-to-power (report §3) ----
    gas_flow_mcf_per_day: float = 2000.0  # Mcf/day available; report §4 (200-20000)
    kwh_per_mcf: float = 11.0             # yield @35-40% genset eff; report §3 (8-14)
    genset_capex_per_kw: float = 1500.0   # USD/kW recip; report §3 Thunder Said (1000-2500)
    genset_opex_per_kwh: float = 0.02     # USD/kWh O&M; report §3 ($15-25/MWh)
    uptime: float = 0.92                  # remote oilfield availability; report §1 (0.85-0.97)

    # ---- fleet hardware: S21 Pro-class default, toggle to XP Hydro (report §1) ----
    miner_hashrate_th: float = 234.0      # Antminer S21 Pro 234 TH/s; report §1
    miner_power_kw: float = 3.51          # S21 Pro 3.51 kW; report §1
    miner_capex: float = 3500.0           # S21 Pro ~$3,000-3,800; report §1
    gpu_power_kw: float = 1.3             # per H100-class GPU incl overhead+cooling; report §2
    gpu_capex: float = 30000.0           # H100-class ~$25-30k; report §2

    # ---- dual-workload allocation (report Key Finding 2) ----
    ai_allocation: float = 0.50           # fraction of FIRM kW to always-on AI baseload; report §4

    # ---- software business model (report §5) ----
    software_fee_mode: str = "perMW"      # "perMW" | "revShare"
    software_fee_per_mw_month: float = 100.0  # USD/MW/mo; Luxor Commander benchmark; report §5
    software_rev_share_pct: float = 0.02      # 2% of gross; report §5 recommends 1-3%

    # ---- finance (report Recommendation 1) ----
    project_years: int = 4
    discount_rate: float = 0.12

    # ---- optional upside, OFF by default (report §3 / Recommendation 5) ----
    carbon_credit_enabled: bool = False
    carbon_price_per_tonne: float = 5.0   # conservative voluntary-market $/tCO2e; upside only


def _npv(rate: float, cashflows: list[float]) -> float:
    """Net present value of a cashflow series (index 0 = t0)."""
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))


def _irr(cashflows: list[float], guess: float = 0.1) -> Optional[float]:
    """Internal rate of return via Newton's method (report §4 irr()).

    Returns None when no IRR exists (no sign change in cashflows) or the
    iteration diverges/overflows.
    """
    if not (any(cf > 0 for cf in cashflows) and any(cf < 0 for cf in cashflows)):
        return None
    rate = guess
    for _ in range(200):
        try:
            f = 0.0
            d = 0.0
            for t, cf in enumerate(cashflows):
                f += cf / (1 + rate) ** t
                if t > 0:
                    d -= t * cf / (1 + rate) ** (t + 1)
        except (OverflowError, ZeroDivisionError):
            return None
        if d == 0:
            return None
        step = f / d
        rate -= step
        if rate <= -0.9999:  # diverging past -100%
            return None
        if abs(step) < 1e-7:
            return rate if -0.9999 < rate < 100 else None
    return None


def compute(inp: RoiInputs) -> dict:
    """Run the dual-workload model. Mirrors the JS in report §4 exactly."""
    # ---------- POWER AVAILABLE (report §4) ----------
    gross_kwh_day = inp.gas_flow_mcf_per_day * inp.kwh_per_mcf
    avg_kw = gross_kwh_day / 24
    firm_kw = avg_kw * inp.uptime

    # ---------- DUAL ALLOCATION: AI baseload, Bitcoin the remainder ----------
    ai_kw = firm_kw * inp.ai_allocation
    btc_kw = firm_kw - ai_kw
    num_gpus = int(ai_kw // inp.gpu_power_kw)
    num_miners = int(btc_kw // inp.miner_power_kw)

    # ---------- DAILY GROSS REVENUE (report §4) ----------
    # BTC: hashprice already bundles subsidy+fees / difficulty (report §4 notes).
    btc_revenue = num_miners * inp.miner_hashrate_th * inp.hashprice_per_th_day * inp.uptime
    ai_revenue = num_gpus * inp.gpu_rental_per_hr * 24 * inp.capture_fraction * inp.uptime
    gross_revenue = btc_revenue + ai_revenue

    # ---------- DAILY OPERATING COST (report §4) ----------
    energy_kwh_day = (ai_kw + btc_kw) * 24
    fuel_mcf_day = energy_kwh_day / inp.kwh_per_mcf
    fuel_cost = fuel_mcf_day * inp.gas_price_per_mcf  # can be negative (report §3)
    genset_om = energy_kwh_day * inp.genset_opex_per_kwh

    site_mw = (ai_kw + btc_kw) / 1000
    if inp.software_fee_mode == "perMW":
        software_fee = site_mw * inp.software_fee_per_mw_month / 30
    else:
        software_fee = gross_revenue * inp.software_rev_share_pct

    # Optional carbon-credit upside, OFF by default (report §3 / Rec 5).
    carbon_revenue = 0.0
    if inp.carbon_credit_enabled:
        carbon_revenue = fuel_mcf_day * CO2E_AVOIDED_PER_MCF * inp.carbon_price_per_tonne

    op_cost = fuel_cost + genset_om + LABOR_HOSTING_USD_PER_DAY + software_fee
    net_profit_day = gross_revenue + carbon_revenue - op_cost

    # ---------- CAPEX (report §4) ----------
    installed_kw = ai_kw + btc_kw
    capex = (
        installed_kw * inp.genset_capex_per_kw
        + num_miners * inp.miner_capex
        + num_gpus * inp.gpu_capex
    )

    # ---------- METRICS (report §4) ----------
    annual_net = net_profit_day * 365
    payback_months = capex / (net_profit_day * 30) if net_profit_day > 0 else None
    cashflows = [-capex] + [annual_net] * inp.project_years
    npv = _npv(inp.discount_rate, cashflows)
    irr = _irr(cashflows)

    return {
        # power
        "firmKw": round(firm_kw, 1),
        "aiKw": round(ai_kw, 1),
        "btcKw": round(btc_kw, 1),
        "numGpus": num_gpus,
        "numMiners": num_miners,
        "siteMw": round(site_mw, 3),
        # revenue (daily)
        "btcRevenue": round(btc_revenue, 2),
        "aiRevenue": round(ai_revenue, 2),
        "carbonRevenue": round(carbon_revenue, 2),
        "grossRevenue": round(gross_revenue + carbon_revenue, 2),
        # cost (daily)
        "fuelCost": round(fuel_cost, 2),
        "gensetOm": round(genset_om, 2),
        "laborHosting": LABOR_HOSTING_USD_PER_DAY,
        "softwareFee": round(software_fee, 2),
        "opCost": round(op_cost, 2),
        # bottom line
        "netProfitDay": round(net_profit_day, 2),
        "annualNet": round(annual_net, 2),
        "capex": round(capex, 2),
        "paybackMonths": round(payback_months, 1) if payback_months is not None else None,
        "npv": round(npv, 2),
        "irr": round(irr, 4) if irr is not None else None,
        # emissions (report §3)
        "co2eAvoidedTonnesYear": round(fuel_mcf_day * CO2E_AVOIDED_PER_MCF * 365, 1),
    }


# Axes that dominate outcomes -> tornado chart (report §4 / Recommendation 1).
SENSITIVITY_AXES = [
    {"key": "btc_price", "label": "BTC Price", "low": 40000.0, "high": 130000.0},
    {"key": "hashprice_per_th_day", "label": "Hashprice", "low": 0.020, "high": 0.10},
    {"key": "gpu_rental_per_hr", "label": "GPU Rental", "low": 1.0, "high": 6.0},
    {"key": "capture_fraction", "label": "AI Capture", "low": 0.40, "high": 0.85},
    {"key": "gas_price_per_mcf", "label": "Gas Price", "low": -1.0, "high": 5.0},
]


def sensitivity(inp: RoiInputs, metric: str = "npv") -> list[dict]:
    """Tornado data: swing of ``metric`` as each axis moves low<->high."""
    base = compute(inp)[metric]
    rows = []
    for axis in SENSITIVITY_AXES:
        low_in = inp.model_copy(update={axis["key"]: axis["low"]})
        high_in = inp.model_copy(update={axis["key"]: axis["high"]})
        low_v = compute(low_in)[metric]
        high_v = compute(high_in)[metric]
        rows.append({
            "label": axis["label"],
            "low": low_v,
            "high": high_v,
            "swing": abs((high_v or 0) - (low_v or 0)),
        })
    rows.sort(key=lambda r: r["swing"], reverse=True)
    return {"base": base, "metric": metric, "rows": rows}
