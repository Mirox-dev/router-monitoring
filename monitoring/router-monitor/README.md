# Router Monitor

Центральный monitor-сервис для OpenWrt-роутеров.

Сервис выполняет короткие allowlisted-проверки через reverse SSH; production-доступ
к роутерам и gateway не включается автоматически.

Планируемые компоненты:

- `collector` — единый пакет allowlisted SSH health checks;
- `scheduler` — цикл с настраиваемым интервалом и временным окном проверки;
- `baseline` — adaptive per-router profiles;
- `bot` — Telegram UI and confirmation flow;
- `repository` — PostgreSQL state/events/audit;
- Prometheus exporter — load, RAM, VSZ/RSS, VPN и transport metrics.

Локальный foundation запускается из `monitoring/compose.yml`. Реальные SSH
ключи, Telegram token и production inventory туда не монтируются; для deployment
будут отдельные secrets и Ansible variables.

Для gateway msk1 поддерживается `transport: tcp_proxy`: отдельные
`cloudflared access tcp` клиенты создают loopback-прокси на портах 2201–2209
в общем network namespace с `router-monitor`. Сборщик подключается прямо к
прокси и не входит по SSH на msk1. Service Token хранится в
`monitoring/secrets/cloudflare-access.env`, который не коммитится.
