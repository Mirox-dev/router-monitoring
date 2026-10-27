from __future__ import annotations

import asyncio
from types import SimpleNamespace

from router_monitor.app import poll_router


class FakeCollector:
    async def collect(self, router, gateway, allowed_ips):
        raise RuntimeError("telemetry command failed")

    async def probe_identity(self, router, gateway):
        return router.id


class FakeInventory:
    def gateway_for(self, router):
        return SimpleNamespace(host="gateway")


def test_telemetry_failure_does_not_mark_a_reachable_router_down() -> None:
    router = SimpleNamespace(id="router-1", display_name="Router 1", gateway="central")

    outcome = asyncio.run(poll_router(router, FakeInventory(), FakeCollector(), set()))

    assert outcome[1] is None
    assert outcome[2] == "telemetry command failed"
    assert outcome[3] is True
