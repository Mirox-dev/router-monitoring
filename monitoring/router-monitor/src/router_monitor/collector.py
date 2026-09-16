from __future__ import annotations

import asyncio
import ipaddress
import re
from dataclasses import dataclass

from .config import Gateway, Router

_COMMAND = r'''#!/bin/sh
printf 'PROCESS='; pidof sing-box 2>/dev/null || true
printf '\nSERVICE='; /etc/init.d/sing-box status 2>&1 || true
printf '\nTUN='; ip link show tun0 2>&1 || true
printf '\nLOAD='; cat /proc/loadavg 2>/dev/null || true
printf '\nMEM='; awk '/MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || true
printf '\nDISK='; df -k /overlay 2>/dev/null | tail -n 1 || true
printf '\nPS='; ps w 2>/dev/null | awk '/[s]ing-box/ {print $3 "," $4 "," $5; exit}' || true
printf '\nPROCD='; ubus call service list '{"name":"sing-box"}' 2>/dev/null || true
printf '\nIP1='; wget -qO- --timeout=8 https://ifconfig.me/ip 2>/dev/null || true
printf '\nIP2='; wget -qO- --timeout=8 https://api.ipify.org 2>/dev/null || true
printf '\n'
'''


@dataclass(slots=True)
class Collected:
    process_ok: bool
    service_ok: bool
    tun_ok: bool
    foreign_ip_ok: bool
    load1: float | None
    cpu_percent: float | None
    vsz_kb: int | None
    rss_kb: int | None
    mem_available_kb: int | None
    disk_used_percent: float | None
    restart_count: int | None
    checks: dict[str, object]


def _number(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group()) if match else None


def _foreign(value: str) -> bool:
    try:
        return ipaddress.ip_address(value.strip()).is_global
    except ValueError:
        return False


def parse_output(output: str) -> Collected:
    fields: dict[str, str] = {}
    current: str | None = None
    keys = {"PROCESS", "SERVICE", "TUN", "LOAD", "MEM", "DISK", "PS", "PROCD", "IP1", "IP2"}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in keys:
            current = key
            fields[key] = value
        elif current:
            fields[current] += line

    ps = [part.strip() for part in fields.get("PS", "").split(",")]
    disk = re.search(r"\s(\d+)%\s", fields.get("DISK", ""))
    restart = re.search(r'"respawn_count"\s*:\s*(\d+)', fields.get("PROCD", ""))
    ips = [fields.get("IP1", "").strip(), fields.get("IP2", "").strip()]
    return Collected(
        process_ok=bool(fields.get("PROCESS", "").strip()),
        service_ok=any(word in fields.get("SERVICE", "").lower() for word in ("running", "started")),
        tun_ok="tun0" in fields.get("TUN", "") and "UP" in fields.get("TUN", ""),
        foreign_ip_ok=len(set(ips)) == 2 and all(_foreign(ip) for ip in ips),
        load1=_number(fields.get("LOAD", "")),
        cpu_percent=_number(ps[0]) if ps else None,
        vsz_kb=int(float(ps[1])) if len(ps) > 1 and ps[1].replace('.', '', 1).isdigit() else None,
        rss_kb=int(float(ps[2])) if len(ps) > 2 and ps[2].replace('.', '', 1).isdigit() else None,
        mem_available_kb=int(_number(fields.get("MEM", "")) or 0) or None,
        disk_used_percent=float(disk.group(1)) if disk else None,
        restart_count=int(restart.group(1)) if restart else None,
        checks={"ip1": ips[0], "ip2": ips[1]},
    )


class SSHCollector:
    def __init__(self, user: str, keys: list[str] | None, timeout: int = 10) -> None:
        self.user, self.keys, self.timeout = user, keys, timeout

    async def collect(self, router: Router, gateway: Gateway) -> Collected:
        import asyncssh

        gateway_conn = await asyncio.wait_for(
            asyncssh.connect(gateway.host, port=gateway.ssh_port, username=self.user, client_keys=self.keys),
            timeout=self.timeout,
        )
        router_conn = None
        try:
            router_conn = await asyncio.wait_for(
                asyncssh.connect("127.0.0.1", port=router.reverse_port, username=self.user, client_keys=self.keys, tunnel=gateway_conn),
                timeout=self.timeout,
            )
            result = await router_conn.run(_COMMAND, check=False)
            if result.exit_status not in (0, None):
                raise RuntimeError(result.stderr.strip() or f"remote command exited {result.exit_status}")
            return parse_output(result.stdout)
        finally:
            if router_conn:
                router_conn.close()
                await router_conn.wait_closed()
            gateway_conn.close()
            await gateway_conn.wait_closed()
