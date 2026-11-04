"""Credential-free regression checks for linked daily TPD performance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import pytest

from app.tpd import ExternalFlow, Valuation, calculate_tpd


START = datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc)


def v(equity, hour, ref, account="alpaca-paper-1", owner="PERSONAL", complete=True):
    try:
        value = Decimal(str(equity))
    except InvalidOperation:
        value = equity
    return Valuation(account, owner, START + timedelta(hours=hour), value, ref, complete)


def f(activity, amount, before, activity_id="cash-1", complete=True):
    return ExternalFlow(activity_id, activity, Decimal(str(amount)), before, complete)


def test_unfunded_account_shows_unavailable_not_zero():
    result = calculate_tpd(v("0", 0, "o"), v("0", 24, "c"))
    assert result.status == "UNAVAILABLE_UNFUNDED"
    assert result.return_pct is None
    assert result.net_pnl == 0


def test_daily_positive_target_without_transfers():
    result = calculate_tpd(v("10000", 0, "o"), v("10010", 24, "c"))
    assert result.status == "CALCULATED"
    assert result.return_pct == Decimal("0.100")
    assert result.net_pnl == Decimal("10")


def test_losses_and_total_loss_are_not_clamped_to_zero():
    assert calculate_tpd(v("10000", 0, "o"), v("9700", 24, "c")).return_pct == Decimal("-3.00")
    assert calculate_tpd(v("10000", 0, "o"), v("0", 24, "c")).return_pct == Decimal("-100")


def test_end_of_day_deposit_is_not_profit():
    result = calculate_tpd(v("10000", 0, "o"), v("12010", 24, "c"), [
        f("CSD", "2000", v("10010", 23, "before"))
    ])
    assert result.return_pct == Decimal("0.100")
    assert result.net_external_flow == Decimal("2000")
    assert result.net_pnl == Decimal("10")


def test_midday_deposit_links_subperiod_returns():
    result = calculate_tpd(v("10000", 0, "o"), v("12120", 24, "c"), [
        f("CSD", "2000", v("10100", 12, "before"))
    ])
    expected = ((Decimal("10100") / Decimal("10000")) *
                (Decimal("12120") / Decimal("12100")) - 1) * 100
    assert result.return_pct == expected
    assert result.net_pnl == Decimal("120")


def test_withdrawal_does_not_appear_as_trading_loss():
    result = calculate_tpd(v("10000", 0, "o"), v("8008", 24, "c"), [
        f("CSW", "-2000", v("10010", 23, "before"))
    ])
    assert Decimal("0") < result.return_pct < Decimal("0.2")
    assert result.net_pnl == Decimal("8")


def test_multiple_flows_have_strict_chronology():
    flows = [
        f("CSD", "100", v("1050", 4, "p1"), "a"),
        f("CSW", "-100", v("1200", 18, "p2"), "b"),
    ]
    result = calculate_tpd(v("1000", 0, "o"), v("1120", 24, "c"), flows)
    assert result.status == "CALCULATED"
    assert result.net_external_flow == 0
    assert result.net_pnl == Decimal("120")
    with pytest.raises(ValueError, match="chronological"):
        calculate_tpd(v("1000", 0, "o"), v("1120", 24, "c"), list(reversed(flows)))


def test_incomplete_evidence_never_yields_reported_return():
    base = v("10000", 0, "o")
    end = v("10010", 24, "c")
    assert calculate_tpd(replace(base, complete=False), end).return_pct is None
    assert calculate_tpd(base, replace(end, complete=False)).status == "INCOMPLETE_EVIDENCE"
    flow = f("CSD", "10", v("10001", 12, "pre"), complete=False)
    assert calculate_tpd(base, v("10020", 24, "c2"), [flow]).return_pct is None
    assert calculate_tpd(base, v("10020", 24, "c2"), [replace(flow, valuation_before=replace(flow.valuation_before, complete=False))]).return_pct is None


def test_owner_and_account_scope_isolation():
    base, end = v("100", 0, "o"), v("110", 24, "c")
    with pytest.raises(ValueError, match="different accounts or owner"):
        calculate_tpd(base, replace(end, owner_scope="BUSINESS"))
    with pytest.raises(ValueError, match="another account or owner"):
        calculate_tpd(base, end, [f("CSD", "10", v("105", 12, "p", owner="BUSINESS"))])
    with pytest.raises(ValueError, match="another account or owner"):
        calculate_tpd(base, end, [f("CSD", "10", v("105", 12, "p", account="other"))])


def test_dividends_and_fees_cannot_be_classed_as_owner_cash():
    with pytest.raises(ValueError, match="external deposits"):
        calculate_tpd(v("100", 0, "o"), v("101", 24, "c"), [
            f("DIV", "1", v("100", 12, "p"))
        ])


@pytest.mark.parametrize("activity,amount", [("CSD", "-1"), ("CSD", "0"), ("CSW", "1"), ("CSW", "0")])
def test_external_flow_sign_is_checked(activity, amount):
    with pytest.raises(ValueError, match="conflicts"):
        calculate_tpd(v("100", 0, "o"), v("101", 24, "c"), [
            f(activity, amount, v("100", 12, "p"))
        ])


def test_activity_ids_and_valuation_provenance_are_unique():
    base, end = v("100", 0, "o"), v("111", 24, "c")
    with pytest.raises(ValueError, match="unique"):
        calculate_tpd(base, end, [f("CSD", "1", v("101", 4, "p1"), "a"),
                                  f("CSD", "1", v("105", 8, "p2"), "a")])
    with pytest.raises(ValueError, match="reused"):
        calculate_tpd(base, end, [f("CSD", "1", v("101", 4, "o"), "a")])


def test_complete_withdrawal_makes_tpd_unavailable():
    result = calculate_tpd(v("100", 0, "o"), v("0", 24, "c"), [
        f("CSW", "-100", v("100", 12, "p"))
    ])
    assert result.return_pct is None
    assert result.status == "UNAVAILABLE_NO_CAPITAL"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "invalid"])
def test_invalid_money_is_rejected(value):
    with pytest.raises(ValueError, match="finite"):
        calculate_tpd(v(value, 0, "o"), v("100", 24, "c"))


def test_naive_dates_and_out_of_window_flows_are_rejected():
    opening, closing = v("100", 0, "o"), v("100", 24, "c")
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_tpd(replace(opening, timestamp=datetime(2026, 11, 2, 21)), closing)
    with pytest.raises(ValueError, match="inside"):
        calculate_tpd(opening, closing, [f("CSD", "1", v("100", 25, "p"))])


def test_negative_equity_and_overwithdrawal_rejected():
    with pytest.raises(ValueError, match="negative account equity"):
        calculate_tpd(v("-1", 0, "o"), v("100", 24, "c"))
    with pytest.raises(ValueError, match="negative post-flow"):
        calculate_tpd(v("100", 0, "o"), v("100", 24, "c"), [
            f("CSW", "-200", v("100", 12, "p"))
        ])


def test_full_withdrawal_then_redeposit_cannot_create_spurious_tpd():
    result = calculate_tpd(v("100", 0, "o"), v("51", 24, "c"), [
        f("CSW", "-100", v("100", 6, "p1"), "a"),
        f("CSD", "50", v("0", 18, "p2"), "b"),
    ])
    assert result.return_pct is None
    assert result.status == "UNAVAILABLE_NO_CAPITAL"
