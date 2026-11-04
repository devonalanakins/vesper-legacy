# TPD% — deterministic account-performance calculation (source-only)

## Scope

TPD means **Trading Percentage Daily**. It is the net time-weighted change in brokerage account equity over an explicitly timestamped period, after excluding *external owner deposits and withdrawals*. It is not banking interest, not a guaranteed daily rate, not an internal reinvestment allocation rule, and not the daily loss circuit breaker.

- `app.tpd.calculate_tpd` is a pure function. It makes no API calls, executes no trading, and cannot move money.
- Opening and closing account equity MUST belong to the same broker account and owner scope and have independently referenced complete, timezone-aware evidence.
- Every explicitly recognized `CSD` (cash deposit) or `CSW` (cash withdrawal) requires a **pre-flow** equity valuation at the transfer instant. The caller must supply events in strict chronological order, with unique activity IDs and valuation references. Dividends, fees, fills and interest are *not* external owner cash flow.
- Result = `(product of (each pre-flow equity / preceding post-flow equity), times closing equity / final post-flow equity - 1) × 100`. Trading costs already reflected in broker equity are included. Net monetary P&L = closing equity - opening equity - net external owner flow.
- `status=CALCULATED` means *the pure arithmetic passed validation against supplied evidence*, not that a real broker feed was independently verified. Its provenance must be established by a separate ingestion/reconciliation adapter before presentation as validated live performance.
- If the broker valuation evidence is incomplete or the period has no positive opening capital, return is **unavailable**, not 0%. If a withdrawal removes all capital mid-period, the daily return is also unavailable under this v1 contract.
- RHEN-strategy attribution is **not** implied by account-level TPD. Multiple strategies sharing one account need independent positions/fills/fee attribution before claiming RHEN-managed performance.

## Independent integration gates

1. Independently reconcile starting and ending equity and CSD/CSW activities with the brokerage. Prove data completeness, temporal alignment and account ownership before setting provenance as validated.
2. Explicitly identify overnight/after-hours valuation convention and the selected portfolio session boundary; TPD itself does not guess trading calendars.
3. For capital-flow days lacking reliable pre-flow valuations, show `UNAVAILABLE` pending reconciliation. Do not silently substitute a non-equivalent `(end - start - cashflows)/start` as exact TPD. Any later Modified Dietz approximation must be separately labeled **ESTIMATED**, never exact.
4. Supply strategy-level attribution and drawdown alongside TPD when available, but do not count Mercury balances or transfers as brokerage return.
5. No capital allocation, broker order submission, live execution, payment authorization, production configuration or migration is enabled by this code.

## Synthetic regression cases

`python -m pytest -q tests/test_tpd.py` validates unfunded zero capital, positive and negative returns, full loss, deposit and withdrawal exclusion, intraday linked returns, scope isolation, explicit event classification, missing and duplicated evidence, and invalid timestamps/money.
