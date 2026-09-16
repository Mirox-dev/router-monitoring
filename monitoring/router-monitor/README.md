# Router Monitor

Первый архитектурный каркас центрального monitor-сервиса BALAGAN.

На текущем этапе зафиксированы контракты и topology; production-доступ к
роутерам и gateway не включается автоматически.

Планируемые компоненты:

- `collector` — allowlisted SSH health checks;
- `scheduler` — transport/runtime/functional/resource intervals;
- `baseline` — adaptive per-router profiles;
- `bot` — Telegram UI and confirmation flow;
- `repository` — PostgreSQL state/events/audit;
- Prometheus exporter — metrics only.

Локальный foundation запускается из `monitoring/compose.yml`. Реальные SSH
ключи, Telegram token и production inventory туда не монтируются; для deployment
будут отдельные secrets и Ansible variables.
