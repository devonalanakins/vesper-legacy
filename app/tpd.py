"""TPD%: owner-scoped, cash-flow-adjusted daily investment performance.

Pure accounting only: this module cannot contact brokers, move cash, or place orders.
Exact time-weighted returns require authoritative valuations immediately BEFORE
all external capital flows. Missing evidence never silently becomes zero return.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable


EXTERNAL_ACTIVITY_TYPES = frozenset({"CSD", "CSW"})


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("TPD monetary values must be finite decimals") from exc
    if not result.is_finite():
        raise ValueError("TPD monetary values must be finite decimals")
    return result


def _validate_stamp(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("TPD valuations require timezone-aware timestamps")


@dataclass(frozen=True)
class Valuation:
    account_id: str
    owner_scope: str
    timestamp: datetime
    equity: Decimal
    evidence_ref: str
    complete: bool = True

    def validate(self) -> Decimal:
        if not self.account_id.strip() or not self.owner_scope.strip() or not self.evidence_ref.strip():
            raise ValueError("TPD valuations need account, owner scope, and evidence reference")
        _validate_stamp(self.timestamp)
        amount = _decimal(self.equity)
        if amount < 0:
            raise ValueError("TPD cannot define return on negative account equity")
        if not isinstance(self.complete, bool):
            raise ValueError("TPD valuation completeness must be boolean")
        return amount


@dataclass(frozen=True)
class ExternalFlow:
    activity_id: str
    activity_type: str
    amount: Decimal
    valuation_before: Valuation
    complete: bool = True


@dataclass(frozen=True)
class TPDResult:
    account_id: str
    owner_scope: str
    start_at: datetime
    end_at: datetime
    status: str
    return_pct: Decimal | None
    net_external_flow: Decimal
    net_pnl: Decimal
    method: str = "linked_time_weighted"
    reason: str | None = None


def calculate_tpd(
    opening: Valuation,
    closing: Valuation,
    flows: Iterable[ExternalFlow] = (),
) -> TPDResult:
    """Link subperiod returns, excluding external deposits and withdrawals.

    Equity is net-of-trading-cost account equity (not only invested notional).
    Each flow carries a contemporaneous, complete valuation BEFORE the flow.
    Flow activity must be explicitly classified as CSD/CSW by the broker adapter.
    This is account performance, not RHEN strategy-specific attribution.
    """
    starting = opening.validate()
    ending = closing.validate()
    if (opening.account_id, opening.owner_scope) != (closing.account_id, closing.owner_scope):
        raise ValueError("TPD may not combine different accounts or owner scopes")
    if opening.timestamp >= closing.timestamp:
        raise ValueError("TPD closing valuation must follow the opening valuation")
    if opening.evidence_ref == closing.evidence_ref:
        raise ValueError("TPD opening and closing valuations need distinct evidence")

    current_start = opening.timestamp
    capital = starting
    growth_factor = Decimal("1")
    net_flow = Decimal("0")
    complete = opening.complete and closing.complete
    seen_ids: set[str] = set()
    seen_refs = {opening.evidence_ref, closing.evidence_ref}
    had_zero_capital_segment = starting == 0

    for flow in flows:
        if not isinstance(flow, ExternalFlow):
            raise ValueError("TPD accepts only explicitly classified external flows")
        if not flow.activity_id.strip() or flow.activity_id in seen_ids:
            raise ValueError("TPD flow activity IDs must be nonempty and unique")
        seen_ids.add(flow.activity_id)
        if flow.activity_type not in EXTERNAL_ACTIVITY_TYPES:
            raise ValueError("TPD only excludes broker-classified external deposits/withdrawals")
        amount = _decimal(flow.amount)
        if (flow.activity_type == "CSD" and amount <= 0) or (
            flow.activity_type == "CSW" and amount >= 0
        ):
            raise ValueError("TPD external flow amount conflicts with broker activity type")
        pre_flow_equity = flow.valuation_before.validate()
        if (flow.valuation_before.account_id, flow.valuation_before.owner_scope) != (
            opening.account_id, opening.owner_scope
        ):
            raise ValueError("TPD cash flow belongs to another account or owner")
        if not current_start < flow.valuation_before.timestamp < closing.timestamp:
            raise ValueError("TPD flows must be chronological and inside the valuation window")
        current_start = flow.valuation_before.timestamp
        if flow.valuation_before.evidence_ref in seen_refs:
            raise ValueError("TPD valuation evidence must not be reused")
        seen_refs.add(flow.valuation_before.evidence_ref)
        if not isinstance(flow.complete, bool):
            raise ValueError("TPD flow evidence completeness must be boolean")
        complete = complete and flow.complete and flow.valuation_before.complete
        net_flow += amount
        if capital > 0:
            growth_factor *= pre_flow_equity / capital
        capital = pre_flow_equity + amount
        had_zero_capital_segment = had_zero_capital_segment or capital == 0
        if capital < 0:
            raise ValueError("TPD flow would create negative post-flow equity")

    net_pnl = ending - starting - net_flow

    def unavailable(status: str, reason: str) -> TPDResult:
        return TPDResult(
            opening.account_id, opening.owner_scope, opening.timestamp, closing.timestamp,
            status, None, net_flow, net_pnl, reason=reason,
        )

    if not complete:
        return unavailable("INCOMPLETE_EVIDENCE", "Required broker valuation or flow evidence is incomplete")
    if starting == 0:
        return unavailable("UNAVAILABLE_UNFUNDED", "No opening investment capital; return is undefined")
    if had_zero_capital_segment or capital == 0:
        return unavailable("UNAVAILABLE_NO_CAPITAL", "A withdrawal removed all invested capital during the period")
    growth_factor *= ending / capital
    return TPDResult(
        account_id=opening.account_id,
        owner_scope=opening.owner_scope,
        start_at=opening.timestamp,
        end_at=closing.timestamp,
        status="CALCULATED",
        return_pct=(growth_factor - Decimal("1")) * Decimal("100"),
        net_external_flow=net_flow,
        net_pnl=net_pnl,
    )
