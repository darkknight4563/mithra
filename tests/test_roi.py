"""Tests for the dual-workload ROI model (defaults trace to the June-2026 report)."""

import pytest

from app.services import roi
from app.services.roi import RoiInputs, compute


def test_defaults_produce_bankable_base_case():
    """Default 1 MW-class site is profitable with a sane payback/IRR/NPV."""
    out = compute(RoiInputs())
    # Fleet sizing from the report defaults (firm ~843 kW, 50/50 split).
    assert out["numGpus"] == 324
    assert out["numMiners"] == 120
    assert out["netProfitDay"] > 0
    assert out["paybackMonths"] is not None and 12 < out["paybackMonths"] < 72
    assert out["npv"] > 0
    assert 0.05 < out["irr"] < 0.40


def test_ai_dominates_revenue():
    """Thesis (Key Finding 1): AI is the prize; it out-earns Bitcoin at defaults."""
    out = compute(RoiInputs())
    assert out["aiRevenue"] > out["btcRevenue"]


def test_negative_gas_price_increases_profit():
    """Stranded gas can be negative-cost (flaring penalty) -> higher profit (§3)."""
    cheap = compute(RoiInputs(gas_price_per_mcf=-1.0))
    dear = compute(RoiInputs(gas_price_per_mcf=5.0))
    assert cheap["netProfitDay"] > dear["netProfitDay"]
    assert cheap["fuelCost"] < 0  # operator is paid to consume the gas


def test_carbon_credit_is_off_by_default_and_additive_when_on():
    """Carbon credits are upside, OFF by default (§3 / Recommendation 5)."""
    base = compute(RoiInputs())
    assert base["carbonRevenue"] == 0.0
    on = compute(RoiInputs(carbon_credit_enabled=True, carbon_price_per_tonne=20.0))
    assert on["carbonRevenue"] > 0
    assert on["netProfitDay"] > base["netProfitDay"]


def test_unprofitable_site_has_no_payback_or_irr():
    """When costs swamp revenue, payback/IRR are reported as None, not garbage."""
    out = compute(RoiInputs(
        gas_price_per_mcf=5.0, gpu_rental_per_hr=1.0,
        capture_fraction=0.40, hashprice_per_th_day=0.02,
    ))
    assert out["netProfitDay"] < 0
    assert out["paybackMonths"] is None
    assert out["irr"] is None


def test_software_fee_modes():
    """perMW and revShare fee modes both produce a positive vendor fee (§5)."""
    per_mw = compute(RoiInputs(software_fee_mode="perMW"))
    rev_share = compute(RoiInputs(software_fee_mode="revShare"))
    assert per_mw["softwareFee"] > 0
    assert rev_share["softwareFee"] > 0
    # 2% of gross is materially larger than $100/MW/mo on a ~0.84 MW site.
    assert rev_share["softwareFee"] > per_mw["softwareFee"]


def test_miner_gross_matches_report_sanity_check():
    """Report §1: S21 XP Hydro (473 TH/s) grosses ~$17/day at $0.036/TH/day."""
    out = compute(RoiInputs(
        miner_hashrate_th=473.0, miner_power_kw=5.676,
        ai_allocation=0.0, uptime=1.0, gas_flow_mcf_per_day=200, kwh_per_mcf=11,
    ))
    per_miner = out["btcRevenue"] / out["numMiners"]
    assert per_miner == pytest.approx(17.03, abs=0.1)


def test_sensitivity_returns_sorted_tornado():
    """Sensitivity returns axes sorted by impact (report §4 tornado)."""
    s = roi.sensitivity(RoiInputs(), metric="npv")
    rows = s["rows"]
    assert len(rows) == len(roi.SENSITIVITY_AXES)
    swings = [r["swing"] for r in rows]
    assert swings == sorted(swings, reverse=True)
