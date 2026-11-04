"""RHEN V5 Replay R1: bounded cost-assumption scorecard from R1 outcomes.

Pure CPU work on unverified hypothetical marks. It does NOT backtest execution,
estimate realizable alpha, fill orders, promote strategies or contact brokers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping

from next_rhen.research.outcomes import SCHEMA as OUTCOMES_SCHEMA, WINDOWS

SCHEMA = "anevum.rhen.paper-cost-scorecard.v1"
_COST_KEYS = frozenset({
    "spread_bps", "entry_slippage_bps", "exit_slippage_bps", "roundtrip_fees_bps",
})
_RESULT_KEYS = frozenset({
    "symbol", "recorded_decision", "horizon_minutes", "due_at", "status",
    "gross_long_reference_bps", "anchor_sha256", "horizon_sha256",
})
_STATUSES = frozenset({
    "OBSERVED_UNATTESTED", "NOT_YET_DUE", "MISSING_ANCHOR", "MISSING_HORIZON",
})
_DECISIONS = frozenset({"QUALIFIED", "REJECTED", "UNMEASURABLE"})
_SOURCE_SHA = re.compile(r"^[0-9a-f]{64}$")


class ReplayContractError(ValueError):
    """Unverified, tampered or insufficient research input cannot be scored."""


def _digest(value: Mapping[str, Any]) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                            allow_nan=False).encode("utf-8")).hexdigest()


def _utc_stamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReplayContractError(f"{field} requires a canonical UTC timestamp")
    try:
        at = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReplayContractError(f"{field} timestamp invalid") from exc
    if at.tzinfo is None or at.utcoffset() != timedelta(0) or (
        at.isoformat().replace("+00:00", "Z") != value
    ):
        raise ReplayContractError(f"{field} timestamp is not canonical UTC")
    return at


def _cost(value: Any, key: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReplayContractError(f"{key} must be a numeric bounded cost")
    try:
        cost = Decimal(str(value))
    except InvalidOperation as exc:
        raise ReplayContractError(f"{key} invalid cost") from exc
    if not cost.is_finite() or not Decimal(0) <= cost <= Decimal(1000):
        raise ReplayContractError(f"{key} is negative, nonfinite or unreasonable")
    return cost


def score_paper_horizons(outcomes: Mapping[str, Any], *, cost_model: Mapping[str, Any]) -> dict:
    """Model costs on independently UNATTESTED gross long-reference marks.

    Returns no strategy PASS, no equity curve, no trade P&L and no order
    authority. Missing outcome rows never become zero-P&L observations.
    """
    if not isinstance(outcomes, dict):
        raise ReplayContractError("outcomes must be an immutable replay document")
    if (outcomes.get("schema_version") != OUTCOMES_SCHEMA or
            outcomes.get("execution_mode") != "PAPER_RESEARCH_ONLY" or
            outcomes.get("source_quality") != "AWAITING_INDEPENDENT_ATTESTATION" or
            outcomes.get("strategy_promotion_allowed") is not False or
            outcomes.get("alpha_validated") is not False or
            outcomes.get("execution_costs_included") is not False or
            outcomes.get("orders_or_positions") is not False or
            outcomes.get("horizons") != list(WINDOWS)):
        raise ReplayContractError("paper source or research authorization contract violated")
    digest = outcomes.get("content_sha256")
    original = {key: value for key, value in outcomes.items() if key != "content_sha256"}
    try:
        computed_digest = _digest(original)
    except (ValueError, TypeError) as exc:
        raise ReplayContractError("malformed outcome document") from exc
    if not isinstance(digest, str) or digest != computed_digest:
        raise ReplayContractError("canonical outcome digest mismatch")
    if not isinstance(cost_model, dict) or set(cost_model) != _COST_KEYS:
        raise ReplayContractError("exact declared cost assumptions required")
    costs = {key: _cost(cost_model[key], key) for key in sorted(_COST_KEYS)}
    roundtrip = sum(costs.values(), Decimal(0))
    records = outcomes.get("results")
    size = outcomes.get("candidate_count")
    if (not isinstance(records, list) or isinstance(size, bool)
            or not isinstance(size, int) or not 0 <= size <= 5000
            or len(records) != size * len(WINDOWS)):
        raise ReplayContractError("full candidate horizon grid is incomplete")

    evaluated = _utc_stamp(outcomes.get("evaluated_at"), "evaluated_at")
    decision_at: datetime | None = None
    decisions_by_symbol: dict[str, str] = {}
    anchors_by_symbol: dict[str, str | None] = {}
    seen: set[tuple[str, int]] = set()
    per_horizon: dict[int, dict] = {
        minutes: {"observed": 0, "missing": 0, "gross": Decimal(0),
                  "net": Decimal(0), "by_decision": {d: 0 for d in _DECISIONS}}
        for minutes in WINDOWS
    }
    statuses = {key: 0 for key in _STATUSES}
    priced = []
    for result in records:
        if not isinstance(result, dict) or set(result) != _RESULT_KEYS:
            raise ReplayContractError("horizon record has unexpected or missing fields")
        symbol, minutes = result["symbol"], result["horizon_minutes"]
        if (not isinstance(symbol, str) or not symbol or
                isinstance(minutes, bool) or minutes not in WINDOWS or
                (symbol, minutes) in seen):
            raise ReplayContractError("duplicate or malformed symbol/horizon")
        seen.add((symbol, minutes))
        status, decision = result["status"], result["recorded_decision"]
        if status not in _STATUSES or decision not in _DECISIONS:
            raise ReplayContractError("unknown observation or decision state")
        due = _utc_stamp(result["due_at"], "due_at")
        inferred_decision_at = due - timedelta(minutes=minutes)
        if decision_at is None:
            decision_at = inferred_decision_at
        elif inferred_decision_at != decision_at:
            raise ReplayContractError("due_at not aligned to the original decision cycle")
        if (status == "NOT_YET_DUE") != (evaluated < due):
            raise ReplayContractError("observation maturity disagrees with evaluated_at")
        if symbol in decisions_by_symbol and decisions_by_symbol[symbol] != decision:
            raise ReplayContractError("recorded decision changed across horizons")
        decisions_by_symbol[symbol] = decision
        anchor_ref, future_ref = result["anchor_sha256"], result["horizon_sha256"]
        for source_name, source_ref in (("anchor", anchor_ref), ("horizon", future_ref)):
            if source_ref is not None and (
                not isinstance(source_ref, str) or
                not _SOURCE_SHA.fullmatch(source_ref)
            ):
                raise ReplayContractError(f"{source_name} SHA256 reference invalid")
        if symbol in anchors_by_symbol and anchors_by_symbol[symbol] != anchor_ref:
            raise ReplayContractError("anchor source changed across horizons")
        anchors_by_symbol[symbol] = anchor_ref
        if status == "NOT_YET_DUE" and future_ref is not None:
            raise ReplayContractError("future source cannot be observed before maturity")
        if status == "MISSING_ANCHOR" and anchor_ref is not None:
            raise ReplayContractError("missing anchor status has an anchor source")
        if status == "MISSING_HORIZON" and (anchor_ref is None or future_ref is not None):
            raise ReplayContractError("missing horizon status has inconsistent sources")
        statuses[status] += 1
        b = per_horizon[minutes]
        is_observed = status == "OBSERVED_UNATTESTED"
        gross = result["gross_long_reference_bps"]
        if is_observed:
            if (not isinstance(gross, (int, float)) or isinstance(gross, bool) or
                    not math.isfinite(gross) or gross < -10000):
                raise ReplayContractError("invalid observed hypothetical gross mark")
            if anchor_ref is None or future_ref is None:
                raise ReplayContractError("observed mark missing referenced source")
            net = Decimal(str(gross)) - roundtrip
            b["observed"] += 1
            b["gross"] += Decimal(str(gross))
            b["net"] += net
            b["by_decision"][decision] += 1
            modeled = float(round(net, 6))
        else:
            if gross is not None:
                raise ReplayContractError("missing source may not claim a realized mark")
            b["missing"] += 1
            modeled = None
        priced.append({
            "symbol": symbol, "recorded_decision": decision,
            "horizon_minutes": minutes, "source_status": status,
            "hypothetical_gross_bps": gross if is_observed else None,
            "hypothetical_net_after_assumed_cost_bps": modeled,
        })
    if statuses != outcomes.get("status_counts") or len(seen) != len(records):
        raise ReplayContractError("count or uniqueness integrity mismatch")
    # Require the same full candidate population at every horizon.
    groups = [{symbol for (symbol, h) in seen if h == minutes} for minutes in WINDOWS]
    if not all(group == groups[0] and len(group) == size for group in groups):
        raise ReplayContractError("horizon-specific survivor filtering is forbidden")

    stats = {}
    for minutes, values in per_horizon.items():
        count = values["observed"]
        stats[str(minutes)] = {
            "observed_candidates": count,
            "missing_candidates": values["missing"],
            "by_recorded_decision": {k: values["by_decision"][k] for k in sorted(_DECISIONS)},
            "mean_gross_reference_bps": float(round(values["gross"]/count, 6)) if count else None,
            "mean_net_assumption_bps": float(round(values["net"]/count, 6)) if count else None,
        }
    return {
        "schema_version": SCHEMA,
        "source_outcome_sha256": digest,
        "workspace_id": outcomes["workspace_id"],
        "run_id": outcomes["run_id"],
        "cycle_id": outcomes["cycle_id"],
        "evaluation_mode": "OFFLINE_PAPER_COUNTERFACTUAL",
        "source_quality": "AWAITING_INDEPENDENT_ATTESTATION",
        "cost_assumptions_bps": {k: float(v) for k, v in costs.items()},
        "modeled_roundtrip_cost_bps": float(roundtrip),
        "horizon_summary": stats,
        "hypothetical_marks": priced,
        "broker_order_authority": False,
        "net_execution_pnl": None,
        "validated_alpha": False,
        "promotion_allowed": False,
    }
