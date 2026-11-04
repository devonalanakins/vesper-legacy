# IREN Discord control-plane interface — foundation v1

Session: `ANEVUM.IREN.BUILD.2026-10-11.001.DISCORD-BOT-FRAMEWORK`

## Decision

Discord is a human-facing interaction and notification transport, **not** the
source of authority or operational truth. IREN's existing FastAPI controller,
canonical event ledger, work engine, and protected-action rules remain the
sources of truth. This foundation adds a signature-verified, read-only Discord
interaction router; it never reads Discord chat, connects to Discord Gateway,
places broker orders, accepts configuration drift, changes jobs, deploys, or
invokes a model. Slack delivery stays unchanged.

## Initial private bot

The `app.iren.discord_interactions.build_discord_router` factory supplies
`POST /v1/iren/discord/interactions` for Discord HTTP Interactions, including
verified PING and four private slash subcommands:

- `/iren status` — current persisted control-plane health and incident count
- `/iren incidents` — latest OPEN incident summaries (bounded)
- `/iren jobs` — status counts from existing IREN work ledger
- `/iren help` — read-only command catalog

Every valid command response is ephemeral with allowed mentions disabled.
Unexpected/unsupported commands never enqueue or execute work. Internal raw
payloads, configuration fingerprint, tokens and provider error messages are not
returned. Failure to fetch durable state displays an explicit unavailable
message rather than synthesizing a healthy status. Results older than 180 seconds
are labeled STALE.

## Installation prerequisites (not configured in this branch)

1. Create a **private** `IREN Operations` Discord application/bot and an
   operator-only Discord server. Do not reuse it for the public Commons server.
2. Register `/iren` with `status`, `incidents`, `jobs`, `help` subcommands
   (guild-scoped for development); command registration is deliberately not
   automatic in this foundation.
3. Add `IREN_DISCORD_ENABLED=true`, `IREN_DISCORD_PUBLIC_KEY` (the hex application
   **public** verification key), `IREN_DISCORD_GUILD_ID`, and
   `IREN_DISCORD_OPERATOR_IDS` (comma-separated Discord user IDs) to the IREN
   service deployment. Never put tokens or webhook URLs in source control.
4. Mount this router in IREN's FastAPI application with callback functions for
   `controller.gateway("iren_read")` and `work_engine.snapshot()`; keep it gated
   by `IREN_DISCORD_ENABLED`. Deploy and verify signed PING, invalid signatures,
   owner-only commands, unavailable upstream, and no privilege escalation.
5. Only after private acceptance, add notification-provider routing to existing
   durable outbox. Dual-deliver to Slack and Discord during migration; retire
   Slack only after measured delivery parity and rollback testing.

## Security invariants

- Verify `X-Signature-Ed25519` and `X-Signature-Timestamp` over original raw bytes.
- Reject unsigned, invalid, stale and oversized requests; require both a
  private-guild match and an explicit Discord operator-user allowlist.
- Never treat Discord administrator/role permissions alone as ANEVUM identity or
  as sufficient authorization for protected actions.
- Never expose broker accounts, strategies, sensitive events, or secrets to a
  community guild; no public bot shares the private bot's credentials.
- Commands initially use bounded 2-second reads so interaction acknowledgment
  remains within Discord's 3-second initial-response limit.
- Bot endpoint cannot mutate work, trading, deployment, configuration, budgets,
  promotions, publication or moderation. Any future action requires an ANEVUM
  identity binding, an explicit human approval flow, audit trail and independent
  policy evaluation before execution.

## Follow-on architecture

`System event -> IREN durable outbox -> notification transport adapter -> Slack + Discord`.
The future public bot uses a different Discord application and sanitized data
projection for Commons support, contributor onboarding, and recognition. ANEVUM
accounts remain the authoritative identity, permissions, contribution and XP
records. No live order path may depend on Discord or an LLM.

## Acceptance

- Unit tests cover fail-closed signature verification, disabled configuration,
  guild/operator isolation, read-only behavior, and output redaction.
- No Railway service, scheduled job, database migration, webhook, bot secret,
  Discord application, or production notification route is created by this PR.
- The existing IREN scheduler, trading constraints, configuration policy, and
  Slack notifications remain unchanged.
