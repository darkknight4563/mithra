"""Tests for the dual-workload ROI engine (app/services/economics.py).

Reference numbers are hand-computed from the report defaults so an auditor can
trace each assertion. Tolerances absorb floating-point rounding only.
"""

import pytest

from app.services.economics import (
    RoiInputs,
    allocate_dual,
    ai_revenue_per_day,
    btc_revenue_per_day,
    co2e_tonnes_avoided_per_year,
    evaluate,
    gas_to_kw,
    irr,
    npv,
    payback_months,
    site_capex,
)


# --------------------------------------------------------------------------
# Pure-function unit tests
# --------------------------------------------------------------------------

def test_gas_to_kw():
    """2000 Mcf/day at 11 kWh/Mcf -> 916.67 kW average."""
    assert gas_to_kw(2000) == pytest.approx(916.667, abs=0.01)
    assert gas_to_kw(0) == 0.0


def test_allocate_dual_splits_firm_and_variable():
    """AI takes 50% of firm power; Bitcoin absorbs the rest of available."""
    ai_kw, btc_kw = allocate_dual(916.667, 0.5, 0.15, 10000)
    assert ai_kw == pytest.approx(389.583, abs=0.01)
    assert btc_kw == pytest.approx(527.083, abs=0.01)
    # The split must conserve total available power.
    assert ai_kw + btc_kw == pytest.approx(916.667, abs=0.01)


def test_allocate_dual_respects_gpu_fleet_cap():
    """A small installed GPU fleet caps AI power; Bitcoin gets the remainder."""
    ai_kw, btc_kw = allocate_dual(916.667, 1.0, 0.0, 100.0)
    assert ai_kw == pytest.approx(100.0, abs=0.01)
    assert btc_kw == pytest.approx(816.667, abs=0.01)


def test_btc_revenue_is_power_limited():
    """Hashrate is power-limited via J/TH; revenue uses $/PH/day (report §1)."""
    usd, th_s = btc_revenue_per_day(527.083, 16, 36, 0.95)
    assert th_s == pytest.approx(32942.7, abs=1)
    assert usd == pytest.approx(1126.6, abs=1)


def test_ai_revenue_counts_whole_gpus():
    """GPU count floors to whole units; revenue applies capture + uptime (§2)."""
    usd, count = ai_revenue_per_day(389.583, 1.3, 2.5, 0.6, 0.98)
    assert count == 299
    assert usd == pytest.approx(10548.7, abs=1)


def test_site_capex_sums_components():
    """Capex = miners + GPUs + generator."""
    cx = site_capex(150, 3520, 299, 30000, 916.667, 1500)
    assert cx == pytest.approx(10_873_000, abs=1000)


def test_payback_handles_nonpositive_net():
    """No payback (None) when daily net is zero or negative — no divide-by-zero."""
    assert payback_months(1_000_000, 0) is None
    assert payback_months(1_000_000, -50) is None
    assert payback_months(300_000, 10_000) == pytest.approx(1.0, abs=0.001)


def test_co2e_avoided_per_year():
    """2000 Mcf/day at 63% reduction avoids ~33,573 tCO2e/yr (report §3)."""
    assert co2e_tonnes_avoided_per_year(2000, 0.63) == pytest.approx(33572.7, abs=1)


# --------------------------------------------------------------------------
# IRR / NPV correctness against known answers
# --------------------------------------------------------------------------

def test_npv_known_values():
    """NPV of [-100, 110] at 10% is exactly 0; undiscounted sum checks out."""
    assert npv(0.10, [-100, 110]) == pytest.approx(0.0, abs=1e-9)
    assert npv(0.0, [-100, 50, 50, 50]) == pytest.approx(50.0)


def test_irr_known_values():
    """IRR of [-100,110] is 10%; [-1000,500,500,500] is ~23.37%."""
    assert irr([-100, 110]) == pytest.approx(0.10, abs=1e-4)
    assert irr([-1000, 500, 500, 500]) == pytest.approx(0.2337, abs=1e-3)


def test_irr_returns_none_without_sign_change():
    """An all-negative cashflow series has no IRR -> None (no crash)."""
    assert irr([-100, -50, -50]) is None


# --------------------------------------------------------------------------
# Full reference scenario (hand-checked) and edge cases
# --------------------------------------------------------------------------

def test_reference_scenario_matches_hand_calc():
    """Default 1 MW-class site reproduces the hand-computed reference numbers."""
    r = evaluate(RoiInputs())
    assert r.available_kw == pytest.approx(916.667, abs=0.1)
    assert r.ai_kw == pytest.approx(389.583, abs=0.1)
    assert r.btc_kw == pytest.approx(527.083, abs=0.1)
    assert r.gpu_count == 299
    assert r.miner_count == 150
    assert r.btc_revenue_day == pytest.approx(1126.6, abs=2)
    assert r.ai_revenue_day == pytest.approx(10548.7, abs=2)
    assert r.gross_revenue_day == pytest.approx(11675.4, abs=3)
    assert r.opex_day == pytest.approx(596.1, abs=1)
    assert r.daily_net == pytest.approx(11079.2, abs=3)
    assert r.capex == pytest.approx(10_873_000, abs=2000)
    assert r.payback_months == pytest.approx(32.7, abs=0.4)
    assert r.irr == pytest.approx(0.180, abs=0.005)
    assert r.npv == pytest.approx(1_409_800, rel=0.03)
    assert r.co2e_tonnes_per_year == pytest.approx(33572.7, abs=2)


def test_with_vs_without_uplift_is_positive_and_first_class():
    """Dual-workload beats naive Bitcoin-only; uplift is reported and positive."""
    r = evaluate(RoiInputs())
    assert r.without_daily_net == pytest.approx(1369.4, abs=2)
    assert r.with_daily_net == r.daily_net
    assert r.uplift_daily > 0
    assert r.uplift_daily == pytest.approx(9709.9, abs=3)
    assert r.uplift_pct is not None and r.uplift_pct > 0


def test_zero_gas_is_just_negative_opex_no_crash():
    """Zero gas: no power, no revenue, net ~ -labor; payback/IRR are None."""
    r = evaluate(RoiInputs(gas_mcf_per_day=0))
    assert r.available_kw == 0.0
    assert r.gpu_count == 0 and r.miner_count == 0
    assert r.gross_revenue_day == 0.0
    assert r.daily_net == pytest.approx(-150.0, abs=0.01)  # labor only
    assert r.payback_months is None
    assert r.irr is None
    assert r.capex == 0.0


def test_ai_only_allocation():
    """ai_baseload_target=1 with no variability sends all power to AI."""
    r = evaluate(RoiInputs(ai_baseload_target=1.0, gas_variability=0.0))
    assert r.btc_kw == pytest.approx(0.0, abs=0.01)
    assert r.btc_revenue_day == 0.0
    assert r.ai_revenue_day > 0


def test_btc_only_allocation():
    """ai_baseload_target=0 sends all power to Bitcoin."""
    r = evaluate(RoiInputs(ai_baseload_target=0.0))
    assert r.ai_kw == pytest.approx(0.0, abs=0.01)
    assert r.ai_revenue_day == 0.0
    assert r.btc_revenue_day > 0


def test_negative_gas_cost_raises_profit():
    """Negative gas cost (paid to consume flare) lifts net vs zero-cost gas."""
    base = evaluate(RoiInputs(gas_cost_usd_per_mcf=0.0))
    paid = evaluate(RoiInputs(gas_cost_usd_per_mcf=-1.0))
    assert paid.daily_net > base.daily_net
    assert paid.opex_day < base.opex_day


def test_revenue_share_fee_mode():
    """Revenue-share fee mode charges a % of gross instead of $/MW (report §5)."""
    r = evaluate(RoiInputs(software_fee_mode="revenue_share", software_revenue_share_pct=0.02))
    assert r.software_fee_day == pytest.approx(r.gross_revenue_day * 0.02, abs=0.01)
