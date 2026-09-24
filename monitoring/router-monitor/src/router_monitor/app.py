from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from prometheus_client import Gauge, start_http_server
from sqlalchemy import select

from .collector import SSHCollector
from .config import load_inventory
from .db import create_schema, make_engine, make_session_factory
from .models import Event, ExternalIP, HealthSample, MonitorCommand, RouterRecord
from .settings import Settings

logger = logging.getLogger(__name__)
transport = Gauge("router_transport_ok", "Reverse SSH transport availability", ["router"])
vpn = Gauge("router_vpn_ok", "Foreign egress IP availability", ["router"])
load = Gauge("router_load1", "Router one minute load average", ["router"])
memory = Gauge("router_mem_available_kb", "Available memory in KiB", ["router"])
vsz = Gauge("router_sing_box_vsz_kb", "sing-box virtual size in KiB", ["router"])


def _identity_matches(router, identity: str) -> bool:
    actual = identity.strip().lower()
    expected = {
        router.id.lower(),
        router.display_name.strip().lower(),
        router.display_name.strip().lower().replace(" ", "-"),
    }
    return bool(actual) and actual in expected


async def discover_gateway(router, inventory, collector: SSHCollector, preferred_key: str | None = None) -> str | None:
    """Find a router on all gateways and return its unique gateway key."""
    candidates = []
    for key in ([preferred_key] if preferred_key else []) + ["central"] + list(inventory.gateways):
        if key and key not in candidates:
            candidates.append(key)

    async def probe(key: str) -> str | None:
        try:
            identity = await collector.probe_identity(router, inventory.gateway_for(router.model_copy(update={"gateway": key})))
            return key if _identity_matches(router, identity) else None
        except Exception:
            return None

    matches = [match for match in await asyncio.gather(*(probe(key) for key in candidates)) if match]
    if len(matches) == 1:
        return matches[0]
    return None


async def relocate_if_needed(router, inventory, collector: SSHCollector, force_scan: bool = False) -> tuple[str | None, str | None]:
    old_gateway = router.gateway
    if not force_scan:
        try:
            await collector.probe_identity(router, inventory.gateway_for(router))
            return None, None
        except Exception:
            pass
    discovered = await discover_gateway(router, inventory, collector, preferred_key=old_gateway)
    if discovered and discovered != old_gateway:
        router.gateway = discovered
        return old_gateway, discovered
    return None, discovered


async def apply_saved_bindings(inventory, sessions) -> None:
    """Keep an automatically discovered gateway across monitor restarts."""
    async with sessions() as session:
        saved = {row.id: row.gateway for row in (await session.execute(select(RouterRecord))).scalars()}
    for router in inventory.routers:
        value = saved.get(router.id)
        if not value:
            continue
        key = value.split(" (", 1)[0]
        if key == "central" or key in inventory.gateways:
            router.gateway = key


async def poll_once(settings: Settings, inventory, sessions, collector: SSHCollector) -> None:
    started = datetime.now(timezone.utc)
    async with sessions() as config_session:
        allowed_ips = {row[0] for row in (await config_session.execute(select(ExternalIP.value))).all()}
    async def poll(router):
        moved_from = moved_to = None
        try:
            result = await collector.collect(router, inventory.gateway_for(router), allowed_ips)
            return router, result, None, moved_from, moved_to
        except Exception as exc:  # one unavailable router must not stop the cycle
            moved_from, moved_to = await relocate_if_needed(router, inventory, collector)
            if moved_to:
                try:
                    result = await collector.collect(router, inventory.gateway_for(router), allowed_ips)
                    return router, result, None, moved_from, moved_to
                except Exception as moved_exc:
                    return router, None, str(moved_exc), moved_from, moved_to
            return router, None, str(exc), moved_from, moved_to

    results = await asyncio.gather(*(poll(router) for router in inventory.routers))
    finished = datetime.now(timezone.utc)
    async with sessions() as session:
        existing = {row.id: row for row in (await session.execute(select(RouterRecord).where(RouterRecord.id.in_([router.id for router in inventory.routers])))).scalars()}
        for router, result, error, moved_from, moved_to in results:
            record = existing.get(router.id)
            if record is None:
                record = RouterRecord(id=router.id)
                session.add(record)
            gateway = inventory.gateway_for(router)
            record.display_name, record.model, record.gateway, record.reverse_port, record.egress_policy = router.display_name, router.model, f"{router.gateway} ({gateway.host})", router.reverse_port, router.egress_policy
            if moved_from and moved_to:
                session.add(Event(router_id=router.id, kind="gateway_changed", severity="info", payload={"from": moved_from, "to": moved_to, "reason": "automatic_discovery"}))
        await session.flush()
        for router, result, error, _, _ in results:
            if result:
                transport.labels(router.id).set(1)
                vpn.labels(router.id).set(int(result.foreign_ip_ok))
                for metric, value in ((load, result.load1), (memory, result.mem_available_kb), (vsz, result.vsz_kb)):
                    if value is not None:
                        metric.labels(router.id).set(value)
                sample = HealthSample(router_id=router.id, collected_at=finished, window_started_at=started, window_finished_at=finished, transport_ok=True, process_ok=result.process_ok, service_ok=result.service_ok, tun_ok=result.tun_ok, foreign_ip_ok=result.foreign_ip_ok, load1=result.load1, cpu_percent=result.cpu_percent, vsz_kb=result.vsz_kb, rss_kb=result.rss_kb, mem_available_kb=result.mem_available_kb, disk_used_percent=result.disk_used_percent, restart_count=result.restart_count, checks=result.checks)
            else:
                transport.labels(router.id).set(0)
                vpn.labels(router.id).set(0)
                sample = HealthSample(router_id=router.id, collected_at=finished, window_started_at=started, window_finished_at=finished, error=error, checks={})
            session.add(sample)
            logger.info("router=%s transport=%s vpn=%s error=%s", router.id, bool(result), result.foreign_ip_ok if result else False, error)
        await session.commit()


async def process_commands(inventory, sessions, collector: SSHCollector) -> None:
    router_map = {router.id: router for router in inventory.routers}
    async with sessions() as session:
        result = await session.execute(select(MonitorCommand).where(MonitorCommand.status == "pending").order_by(MonitorCommand.created_at).with_for_update(skip_locked=True).limit(5))
        commands = list(result.scalars())
        for command in commands:
            command.status = "running"
            command.started_at = datetime.now(timezone.utc)
        await session.commit()
    for command in commands:
        router = router_map.get(command.router_id)
        finished = datetime.now(timezone.utc)
        payload: dict[str, object]
        status = "failed"
        try:
            if router is None:
                raise ValueError("router is not present in inventory")
            moved_from = moved_to = None
            if command.action == "health_check":
                async with sessions() as config_session:
                    allowed_ips = {row[0] for row in (await config_session.execute(select(ExternalIP.value))).all()}
                moved_from, moved_to = await relocate_if_needed(router, inventory, collector, force_scan=True)
                collected = await collector.collect(router, inventory.gateway_for(router), allowed_ips)
                payload = {"process_ok": collected.process_ok, "tun_ok": collected.tun_ok, "foreign_ip_ok": collected.foreign_ip_ok}
                if moved_from and moved_to:
                    payload.update({"gateway_changed": True, "from": moved_from, "to": moved_to})
            elif command.action == "restart_sing_box":
                payload = {"message": await collector.restart_sing_box(router, inventory.gateway_for(router))}
            else:
                raise ValueError("action is not allowlisted")
            status = "completed"
        except Exception as exc:
            payload = {"error": str(exc)}
        async with sessions() as session:
            current = await session.get(MonitorCommand, command.id)
            if current:
                current.status, current.result, current.finished_at = status, payload, finished
                if moved_from and moved_to:
                    session.add(Event(router_id=router.id, kind="gateway_changed", severity="info", payload={"from": moved_from, "to": moved_to, "reason": "manual_check"}))
                    record = await session.get(RouterRecord, router.id)
                    if record:
                        gateway = inventory.gateway_for(router)
                        record.gateway = f"{router.gateway} ({gateway.host})"
                await session.commit()


async def run(settings: Settings) -> None:
    inventory = load_inventory(settings.inventory)
    engine = make_engine(settings.database_url)
    await create_schema(engine)
    sessions = make_session_factory(engine)
    await apply_saved_bindings(inventory, sessions)
    start_http_server(settings.metrics_port, addr=settings.metrics_host)
    collector = SSHCollector(settings.ssh_user, settings.client_keys, settings.ssh_connect_timeout_seconds, settings.ssh_known_hosts)
    while True:
        await poll_once(settings, inventory, sessions, collector)
        await process_commands(inventory, sessions, collector)
        await asyncio.sleep(settings.poll_interval_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(Settings()))
