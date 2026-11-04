"""Read-only Discord interaction adapter for the existing IREN control plane.

No Discord role, message, or command ever grants production mutation authority.
Enable only after configuring the Discord application public key and explicit
private-guild/operator ID allowlists.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import time
from typing import Any, Awaitable, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, HTTPException, Request

Read = Callable[[], Awaitable[dict[str, Any]]]
_EPHEMERAL = 64
_MAX_REQUEST_BYTES = 32_768
_MAX_CLOCK_SKEW_SECONDS = 300
_READ_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class DiscordConfig:
    enabled: bool = False
    public_key: str = ""
    guild_id: str = ""
    operator_ids: frozenset[str] = frozenset()
    stale_after_seconds: int = 180

    @classmethod
    def from_env(cls) -> "DiscordConfig":
        return cls(
            enabled=os.getenv("IREN_DISCORD_ENABLED", "").lower() in {"1", "true", "yes"},
            public_key=os.getenv("IREN_DISCORD_PUBLIC_KEY", "").strip(),
            guild_id=os.getenv("IREN_DISCORD_GUILD_ID", "").strip(),
            operator_ids=frozenset(
                part.strip()
                for part in os.getenv("IREN_DISCORD_OPERATOR_IDS", "").split(",")
                if part.strip()
            ),
        )


def _verify(config: DiscordConfig, timestamp: str, signature: str, raw_body: bytes) -> None:
    try:
        sent_at = int(timestamp)
        if abs(time.time() - sent_at) > _MAX_CLOCK_SKEW_SECONDS:
            raise ValueError("expired interaction")
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(config.public_key))
        key.verify(bytes.fromhex(signature), timestamp.encode("ascii") + raw_body)
    except (ValueError, InvalidSignature, UnicodeError) as exc:
        raise HTTPException(status_code=401, detail="invalid_discord_signature") from exc


def _reply(text: str) -> dict[str, Any]:
    return {
        "type": 4,
        "data": {
            "content": text[:1900],
            "flags": _EPHEMERAL,
            "allowed_mentions": {"parse": []},
        },
    }


def _authorized(config: DiscordConfig, event: dict[str, Any]) -> bool:
    member = event.get("member")
    user = member.get("user") if isinstance(member, dict) else None
    return (
        bool(config.guild_id and config.operator_ids)
        and event.get("guild_id") == config.guild_id
        and isinstance(user, dict)
        and str(user.get("id") or "") in config.operator_ids
    )


def _current(stamp: Any, max_age_seconds: int) -> bool:
    try:
        observed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return False
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        return 0 <= age <= max_age_seconds
    except (TypeError, ValueError):
        return False


def _active_incidents(state: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    incidents = state.get("incidents") or {}
    if not isinstance(incidents, dict):
        return []
    return [
        (str(key), value)
        for key, value in sorted(incidents.items())
        if isinstance(value, dict) and value.get("status") == "OPEN"
    ]


def _render_status(saved: dict[str, Any], stale_after: int) -> str:
    state = saved.get("state") or {}
    if not isinstance(state, dict):
        state = {}
    age = "CURRENT" if _current(state.get("observed_at"), stale_after) else "STALE"
    return (
        "IREN CONTROL PLANE\n"
        f"Health: {str(state.get('state') or 'UNKNOWN')[:40]} ({age})\n"
        f"Open incidents: {len(_active_incidents(state))}\n"
        f"Revision: {str(saved.get('revision') or 0)[:20]}\n"
        f"Observed: {str(state.get('observed_at') or 'unavailable')[:40]}\n"
        "Read-only. Source of truth: ANEVUM IREN."
    )


def _render_incidents(saved: dict[str, Any]) -> str:
    state = saved.get("state") or {}
    if not isinstance(state, dict):
        state = {}
    active = _active_incidents(state)
    if not active:
        return "IREN INCIDENTS\nNo open incidents in the latest recorded state."
    lines = [f"IREN INCIDENTS — {len(active)} open"]
    for key, row in active[:8]:
        severity = str(row.get("severity") or "unknown")[:24]
        reason = str(row.get("reason") or "unknown")[:100]
        lines.append(f"- {key[:90]} [{severity}]: {reason}")
    if len(active) > 8:
        lines.append(f"... and {len(active) - 8} more in ANEVUM Command")
    return "\n".join(lines)


def _render_jobs(snapshot: dict[str, Any]) -> str:
    jobs = snapshot.get("jobs") or []
    if not isinstance(jobs, list):
        jobs = []
    counts: dict[str, int] = {}
    for row in jobs:
        if isinstance(row, dict):
            status = str(row.get("status") or "UNKNOWN").upper()
            counts[status] = counts.get(status, 0) + 1
    return (
        "IREN JOBS\n"
        f"Active/queued: {sum(counts.get(k, 0) for k in ('ACTIVE', 'RUNNING', 'QUEUED'))}\n"
        f"Waiting/blocked: {sum(counts.get(k, 0) for k in ('WAITING', 'BLOCKED'))}\n"
        f"Needs human approval: {counts.get('NEEDS_APPROVAL', 0)}\n"
        "This interface cannot approve or execute jobs."
    )


def build_discord_router(
    read_state: Read,
    read_work: Read,
    *,
    config: DiscordConfig | None = None,
) -> APIRouter:
    """Attach to IREN FastAPI only when explicitly enabled; no Discord Gateway needed."""
    settings = config or DiscordConfig.from_env()
    router = APIRouter()

    @router.post("/v1/iren/discord/interactions")
    async def interaction(request: Request) -> dict[str, Any]:
        if not settings.enabled:
            raise HTTPException(status_code=404, detail="not_found")
        if not settings.public_key:
            raise HTTPException(status_code=503, detail="discord_public_key_not_configured")
        raw = await request.body()
        if len(raw) > _MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="interaction_too_large")
        _verify(
            settings,
            request.headers.get("X-Signature-Timestamp", ""),
            request.headers.get("X-Signature-Ed25519", ""),
            raw,
        )
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(status_code=400, detail="invalid_interaction_json") from exc
        if not isinstance(event, dict):
            raise HTTPException(status_code=400, detail="invalid_interaction_json")
        if event.get("type") == 1:
            return {"type": 1}
        if event.get("type") != 2 or not _authorized(settings, event):
            return _reply("This IREN interface is restricted to authorized operators.")
        data = event.get("data") or {}
        if not isinstance(data, dict) or data.get("name") != "iren":
            return _reply("Unknown command. Use /iren help.")
        options = data.get("options") or []
        choice = options[0].get("name") if isinstance(options, list) and options and isinstance(options[0], dict) else "help"
        if choice == "help":
            return _reply("IREN commands: /iren status, /iren incidents, /iren jobs, /iren help. All are read-only.")
        if choice not in {"status", "incidents", "jobs"}:
            return _reply("Unsupported command. No work was queued or executed.")
        try:
            if choice == "jobs":
                snapshot = await asyncio.wait_for(read_work(), timeout=_READ_TIMEOUT_SECONDS)
                return _reply(_render_jobs(snapshot))
            saved = await asyncio.wait_for(read_state(), timeout=_READ_TIMEOUT_SECONDS)
            if choice == "status":
                return _reply(_render_status(saved, settings.stale_after_seconds))
            return _reply(_render_incidents(saved))
        except (Exception):
            # Do not expose internal endpoints, credentials or provider errors.
            return _reply("IREN data is temporarily unavailable. Check ANEVUM Command.")

    return router
