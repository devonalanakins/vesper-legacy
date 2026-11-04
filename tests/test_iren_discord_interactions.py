from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import time
from unittest.mock import AsyncMock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
import httpx

from app.iren.discord_interactions import DiscordConfig, build_discord_router


class DiscordHarness:
    def __init__(self, *, enabled=True):
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()
        self.state = AsyncMock(return_value={
            "revision": 9,
            "state": {
                "state": "DEGRADED",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "incidents": {"workflow.daily": {
                    "status": "OPEN", "severity": "warning", "reason": "missed"
                }},
                "configuration_baseline": "DO_NOT_EXPOSE",
            },
        })
        self.work = AsyncMock(return_value={"jobs": [
            {"status": "QUEUED"}, {"status": "WAITING"},
            {"status": "NEEDS_APPROVAL"},
        ]})
        self.app = FastAPI()
        self.app.include_router(build_discord_router(
            self.state, self.work,
            config=DiscordConfig(
                enabled=enabled,
                public_key=public,
                guild_id="server-1",
                operator_ids=frozenset({"owner-1"}),
            ),
        ))

    async def send(self, payload, *, sign=True, timestamp=None):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        ts = str(timestamp if timestamp is not None else int(time.time()))
        sig = self.private.sign(ts.encode() + raw).hex() if sign else "00" * 64
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            return await client.post(
                "/v1/iren/discord/interactions", content=raw,
                headers={"X-Signature-Timestamp": ts, "X-Signature-Ed25519": sig},
            )


def command(name, *, member="owner-1", guild="server-1"):
    return {
        "type": 2, "guild_id": guild,
        "member": {"user": {"id": member}},
        "data": {"name": "iren", "options": [{"name": name, "type": 1}]},
    }


def run(coroutine):
    return asyncio.run(coroutine)


def test_default_disabled_route_is_nonoperational():
    h = DiscordHarness(enabled=False)
    assert run(h.send({"type": 1})).status_code == 404


def test_signed_ping_is_acknowledged():
    h = DiscordHarness()
    response = run(h.send({"type": 1}))
    assert response.status_code == 200
    assert response.json() == {"type": 1}


def test_invalid_or_expired_signature_fails_closed():
    h = DiscordHarness()
    assert run(h.send({"type": 1}, sign=False)).status_code == 401
    assert run(h.send({"type": 1}, timestamp=int(time.time()) - 800)).status_code == 401


def test_private_guild_and_operator_are_both_required():
    h = DiscordHarness()
    for event in (command("status", member="stranger"), command("status", guild="public"),
                  {**command("status"), "member": None}):
        response = run(h.send(event))
        assert response.status_code == 200
        assert response.json()["data"]["flags"] == 64
        assert "restricted" in response.json()["data"]["content"]
    h.state.assert_not_called()


def test_read_only_commands_render_redacted_data():
    h = DiscordHarness()
    status = run(h.send(command("status"))).json()["data"]["content"]
    incidents = run(h.send(command("incidents"))).json()["data"]["content"]
    jobs = run(h.send(command("jobs"))).json()["data"]["content"]
    assert "DEGRADED (CURRENT)" in status
    assert "DO_NOT_EXPOSE" not in status
    assert "workflow.daily" in incidents
    assert "Needs human approval: 1" in jobs
    assert h.state.await_count == 2
    assert h.work.await_count == 1


def test_mutation_commands_never_queue_work():
    h = DiscordHarness()
    for name in ("deploy", "approve", "trade", "execute", "accept"):
        result = run(h.send(command(name))).json()
        assert "No work was queued" in result["data"]["content"]
    h.state.assert_not_called()
    h.work.assert_not_called()


def test_upstream_exception_does_not_leak_details():
    h = DiscordHarness()
    h.state.side_effect = RuntimeError("secret=VERY_PRIVATE")
    body = run(h.send(command("status"))).json()["data"]["content"]
    assert "temporarily unavailable" in body
    assert "VERY_PRIVATE" not in body
