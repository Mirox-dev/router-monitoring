from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from prometheus_client import Gauge, start_http_server
from sqlalchemy import select

from .collector import SSHCollector
from .config import load_inventory
from .db import create_schema, make_engine, make_session_factory
from .models import HealthSample, MonitorCommand, RouterRecord
from .settings import Settings

logger = logging.getLogger(__name__)
transport = Gauge("router_transport_ok", "Reverse SSH transport availability", ["router"])
vpn = Gauge("router_vpn_ok", "Foreign egress IP availability", ["router"])
load = Gauge("router_load1", "Router one minute load average", ["router"])
memory = Gauge("router_mem_available_kb", "Available memory in KiB", ["router"])
vsz = Gauge("router_sing_box_vsz_kb", "sing-box virtual size in KiB", ["router"])


async def poll_once(settings: Settings, inventory, sessions, collector: SSHCollector) -> None:
    started = datetime.now(timezone.utc)
    async def poll(router):
        try:
            result = await collector.collect(router, inventory.gateway_for(router))
            return router, result, None
        except Exception as exc:  # one unavailable router must not stop the cycle
            return router, None, str(exc)

    results = await asyncio.gather(*(poll(router) for router in inventory.routers))
    finished = datetime.now(timezone.utc)
    async with sessions() as session:
        for router, result, error in results:
            await session.merge(RouterRecord(id=router.id, display_name=router.display_name, model=router.model, gateway=router.gateway, reverse_port=router.reverse_port, egress_policy=router.egress_policy))
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
            if command.action == "health_check":
                collected = await collector.collect(router, inventory.gateway_for(router))
                payload = {"process_ok": collected.process_ok, "tun_ok": collected.tun_ok, "foreign_ip_ok": collected.foreign_ip_ok}
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
                await session.commit()


async def run(settings: Settings) -> None:
    inventory = load_inventory(settings.inventory)
    engine = make_engine(settings.database_url)
    await create_schema(engine)
    sessions = make_session_factory(engine)
    start_http_server(settings.metrics_port, addr=settings.metrics_host)
    collector = SSHCollector(settings.ssh_user, settings.client_keys, settings.ssh_connect_timeout_seconds)
    while True:
        await poll_once(settings, inventory, sessions, collector)
        await process_commands(inventory, sessions, collector)
        await asyncio.sleep(settings.poll_interval_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(Settings()))
