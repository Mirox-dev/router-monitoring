from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from prometheus_client import Gauge, start_http_server
from sqlalchemy import select

from .collector import SSHCollector
from .config import load_inventory
from .db import create_schema, make_engine, make_session_factory
from .models import Event, ExternalIP, HealthSample, MonitorCommand, RouterRecord
from .settings import Settings

logger = logging.getLogger(__name__)
transport = Gauge("router_transport_ok", "Reverse SSH transport availability", ["router"])
collection = Gauge("router_collection_ok", "Full router telemetry collection succeeded", ["router"])
egress_check = Gauge("router_egress_check_ok", "Both external IP providers returned valid IPs", ["router"])
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
        except Exception:  # gateway connection failures vary by backend
            logger.debug("Gateway probe failed for router=%s gateway=%s", router.id, key, exc_info=True)
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
        except Exception:  # probe failure triggers gateway discovery
            logger.debug("Current gateway probe failed for router=%s", router.id, exc_info=True)
    discovered = await discover_gateway(router, inventory, collector, preferred_key=old_gateway)
    if discovered and discovered != old_gateway:
        router.gateway = discovered
        return old_gateway, discovered
    return None, discovered


async def poll_router(router, inventory, collector: SSHCollector, allowed_ips: set[str]):
    """Separate tunnel reachability from failures in the heavier telemetry command."""
    try:
        result = await collector.collect(router, inventory.gateway_for(router), allowed_ips)
        return router, result, None, True, None, None
    except Exception as exc:  # noqa: BLE001 — SSH and remote command errors vary by backend
        error = str(exc)

    try:
        identity = await collector.probe_identity(router, inventory.gateway_for(router))
        if _identity_matches(router, identity):
            return router, None, error, True, None, None
    except Exception:
        logger.debug("Identity probe failed for router=%s", router.id, exc_info=True)

    old_gateway = router.gateway
    moved_from, moved_to = await relocate_if_needed(router, inventory, collector, force_scan=True)
    transport_ok = bool(moved_to)
    if moved_to and moved_to != old_gateway:
        try:
            result = await collector.collect(router, inventory.gateway_for(router), allowed_ips)
            return router, result, None, True, moved_from, moved_to
        except Exception as moved_exc:  # noqa: BLE001 — isolate this router's second attempt
            error = str(moved_exc)
    return router, None, error, transport_ok, moved_from, moved_to


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
    started = datetime.now(UTC)
    async with sessions() as config_session:
        allowed_ips = {row[0] for row in (await config_session.execute(select(ExternalIP.value))).all()}
    results = await asyncio.gather(*(poll_router(router, inventory, collector, allowed_ips) for router in inventory.routers))
    finished = datetime.now(UTC)
    async with sessions() as session:
        existing = {row.id: row for row in (await session.execute(select(RouterRecord).where(RouterRecord.id.in_([router.id for router in inventory.routers])))).scalars()}
        for router, result, error, _, moved_from, moved_to in results:
            record = existing.get(router.id)
            if record is None:
                record = RouterRecord(id=router.id)
                session.add(record)
            gateway = inventory.gateway_for(router)
            record.display_name, record.model, record.gateway, record.reverse_port, record.egress_policy = router.display_name, router.model, f"{router.gateway} ({gateway.host})", router.reverse_port, router.egress_policy
            if moved_from and moved_to:
                session.add(Event(router_id=router.id, kind="gateway_changed", severity="info", payload={"from": moved_from, "to": moved_to, "reason": "automatic_discovery"}))
        await session.flush()
        for router, result, error, transport_ok, _, _ in results:
            transport.labels(router.id).set(int(transport_ok))
            if result:
                collection.labels(router.id).set(1)
                egress_check.labels(router.id).set(int(result.egress_check_ok))
                if result.egress_check_ok:
                    vpn.labels(router.id).set(int(result.foreign_ip_ok))
                for metric, value in ((load, result.load1), (memory, result.mem_available_kb), (vsz, result.vsz_kb)):
                    if value is not None:
                        metric.labels(router.id).set(value)
                checks = {**result.checks, "telemetry_stale": False}
                sample = HealthSample(router_id=router.id, collected_at=finished, window_started_at=started, window_finished_at=finished, transport_ok=True, process_ok=result.process_ok, service_ok=result.service_ok, tun_ok=result.tun_ok, foreign_ip_ok=result.foreign_ip_ok, load1=result.load1, cpu_percent=result.cpu_percent, vsz_kb=result.vsz_kb, rss_kb=result.rss_kb, mem_available_kb=result.mem_available_kb, disk_used_percent=result.disk_used_percent, restart_count=result.restart_count, checks=checks)
            else:
                collection.labels(router.id).set(0)
                egress_check.labels(router.id).set(0)
                previous = (await session.execute(select(HealthSample).where(HealthSample.router_id == router.id).order_by(HealthSample.collected_at.desc()).limit(1))).scalars().first()
                previous_checks = dict(previous.checks or {}) if previous else {}
                previous_checks.update({"telemetry_stale": True, "egress_check_ok": False})
                sample = HealthSample(router_id=router.id, collected_at=finished, window_started_at=started, window_finished_at=finished, transport_ok=transport_ok, process_ok=previous.process_ok if previous else False, service_ok=previous.service_ok if previous else False, tun_ok=previous.tun_ok if previous else False, foreign_ip_ok=previous.foreign_ip_ok if previous else False, load1=previous.load1 if previous else None, cpu_percent=previous.cpu_percent if previous else None, vsz_kb=previous.vsz_kb if previous else None, rss_kb=previous.rss_kb if previous else None, mem_available_kb=previous.mem_available_kb if previous else None, disk_used_percent=previous.disk_used_percent if previous else None, restart_count=previous.restart_count if previous else None, checks=previous_checks, error=error)
            session.add(sample)
            logger.info("router=%s transport=%s collection=%s egress_check=%s vpn=%s error=%s", router.id, transport_ok, bool(result), result.egress_check_ok if result else False, result.foreign_ip_ok if result and result.egress_check_ok else None, error)
        await session.commit()


async def process_commands(inventory, sessions, collector: SSHCollector) -> None:
    router_map = {router.id: router for router in inventory.routers}
    async with sessions() as session:
        result = await session.execute(select(MonitorCommand).where(MonitorCommand.status == "pending").order_by(MonitorCommand.created_at).with_for_update(skip_locked=True).limit(5))
        commands = list(result.scalars())
        for command in commands:
            command.status = "running"
            command.started_at = datetime.now(UTC)
        await session.commit()
    for command in commands:
        router = router_map.get(command.router_id)
        finished = datetime.now(UTC)
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
        except Exception as exc:  # noqa: BLE001 — persist command failures for the bot
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
