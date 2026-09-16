# Router Monitoring

Отдельный control plane для мониторинга и безопасного управления роутерами
OpenWrt, подключёнными к шлюзам через reverse SSH.

Центральный экземпляр разворачивается на центральном сервере. На роутерах
не устанавливается агент: сборщик выполняет небольшой пакет разрешённых SSH
команд, а временные данные и история хранятся на сервере.

## Состав

- PostgreSQL — состояние роутеров, проверки, события и аудит;
- Prometheus — временные ряды нагрузки и технических метрик;
- Grafana — графики и dashboards;
- Alertmanager — маршрутизация уведомлений;
- `router-monitor` — Python-сервис, SSH-сборщик и health-check pipeline;
- Telegram Bot — интерфейс диагностики и подтверждённых действий.

Архитектура и границы MVP описаны в [docs/router-monitoring.md](docs/router-monitoring.md),
архитектурное решение — в [ADR 0017](docs/adr/0017-router-monitoring-control-plane.md).

## Локальный запуск инфраструктуры

```powershell
Copy-Item monitoring/.env.example monitoring/.env
docker compose --env-file monitoring/.env -f monitoring/compose.yml up -d postgres prometheus grafana alertmanager
```

Секреты и реальные адреса не хранятся в репозитории. Перед deployment необходимо
заполнить inventory и отдельно подтвердить изменения на реальных серверах.
