from router_monitor.collector import parse_output


def test_parse_healthy_router_output() -> None:
    result = parse_output(
        """PROCESS=123
SERVICE=running
TUN=4: tun0: <POINTOPOINT,UP,LOWER_UP>
LOAD=0.30 0.20 0.10 1/50 123
MEM=104857
DISK=/dev/root 10000 4000 6000 40% /overlay
PS=77.0,140000,90000
PROCD={\"respawn_count\":2}
IP1=8.8.8.8
IP2=1.1.1.1
"""
    )
    assert result.process_ok and result.service_ok and result.tun_ok
    assert result.foreign_ip_ok
    assert result.load1 == 0.3
    assert result.vsz_kb == 140000 and result.rss_kb == 90000
    assert result.restart_count == 2


def test_private_ip_is_not_foreign() -> None:
    result = parse_output("PROCESS=1\nIP1=192.168.1.1\nIP2=8.8.8.8\n")
    assert not result.foreign_ip_ok
