# ANEVUM V5 — RHEN Replay R1: cost-aware, never-executed paper reference scorecards

**Status:** Development primitive only. **Source:** `next_rhen/replay/scorecard.py` consumes `next_rhen/research/outcomes.py`; both are separate from legacy VELUM/GRAEN/NOSTRA deployments. [Unified V5 program](https://github.com/anevum/anevum-web/issues/259).

## What is modeled

The Replay scorer takes a complete R1 outcome document, verifies its canonical content SHA, its declared paper-only, unverified-source, no-trade and no-promotion flags, and requires **every candidate at every 5/15/60-minute window**. Qualified, rejected and unmeasurable candidates are all included to avoid hiding nonselected opportunities.

A declared static research cost model requires four bounded numeric nonnegative assumptions: **spread**, entry slippage, exit slippage and round-trip fees (in basis points). A hypothetical long reference net value is simply the observed *gross reference mark* minus the sum of the assumed costs; neither the entry nor the exit is a simulated actual fill. The output shows the observed/absent candidate count and mean gross and modeled net marks **only for supplied, observed horizons**, with a separated by-decision observed count. Missing marks are `null`, never zeros.

## What it is not

- No executed trades, fill simulator, brokerage balance, spread history, portfolio ledger, user-specific capital, margin, shorting, copied trades, or broker connection.
- No validated strategy, training/validation/holdout promotion, risk-adjusted edge, capital governor, order routing, independently attested market source or production-ready execution path.
- A source SHA proves at most internal deterministic data consistency; it cannot authenticate an off-host broker snapshot or complete upstream scanner market data.

The returned scorecard preserves `net_execution_pnl: null`, `broker_order_authority: false`, `validated_alpha: false`, `promotion_allowed: false`, and `source_quality: AWAITING_INDEPENDENT_ATTESTATION`. Live or promoted source records, tampered SHA, missing modeled costs, duplicate/scoped-out symbols, horizon-specific survivor filtering, negative/unbounded costs and fabricated zero gross returns are rejected.

Replay now also independently checks canonical UTC due/evaluation timestamps, consistent original decision time and recorded decision across every symbol's horizons, unchanged anchor provenance, valid lowercase source SHA256 references and missing-versus-observed maturity status. Those invariants reject source documents with internally forged row semantics even if a new digest has been computed; they do not attest the source or establish genuine alpha. Tests exercise all these denial cases and correctly immature null-valued outcomes.

## Next acceptance

Connect these pure functions to independently attested, timestamped full-population raw evidence after real paper read-only source validation. Then complete deterministic executable replay, spread/slippage calibration, out-of-sample testing, and evidence archive restore. Trading stays off until separately authorized.


## Strict input envelope and bounded hypothetical marks — October 10 follow-up

The offline Replay entry point now enforces **exact v1 envelope fields**, stable workspace/run/cycle identifiers, a positive integer sequence and a lowercase decision-cycle SHA256 reference. The horizon grid must use actual canonical U.S. equity symbol identifiers and exact integer horizon keys; recomputed digests do not bypass type checks. Recorded status counters must be nonnegative real integers (never Python booleans), the evaluated timestamp cannot precede the original decision even when every horizon is not yet due, and gross hypothetical reference returns must be finite within -10,000 to +1,000,000 bps. The sibling Research producer rejects more extreme price ratios before serialization or float overflow.

These are bounds on **unverified research input integrity**, not an economic validation of results. No upstream market-source attestation, executable fill parity, risk authorization, real-money P&L or automated promotion is introduced. Existing PAPER_RESEARCH_ONLY safety flags and all untouched-live-trading requirements remain in force.
