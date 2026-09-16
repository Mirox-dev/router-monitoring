# BALAGAN Router Monitoring

## Цель

Центральный сервис на `aeza.balaganet.site` контролирует OpenWrt-роутеры,
которые выбирают gateway по retry-списку и публикуют SSH reverse tunnel на
`127.0.0.1:220x`. На роутерах не хранятся история, база данных или постоянный
мониторинговый агент.

## Топология

```text
Telegram Bot + Monitor + PostgreSQL + Prometheus/Grafana/Alertmanager
                         (aeza.balaganet.site)
                         /        |        \
             SSH gateway 1   SSH gateway 2   локальные сервисы
                   |               |
             127.0.0.1:220x   127.0.0.1:220x
                   \               /
                    OpenWrt routers
```

`aeza` — единственная точка запуска Telegram-бота и источник истины для
состояния мониторинга. Gateway-серверы сохраняют только собственные systemd
journal и обслуживают reverse SSH.

## Слои проверок

1. **Transport** — reverse listener существует на правильном gateway и порту.
2. **Runtime** — процесс sing-box, procd-статус, `tun0`, uptime, restart count.
3. **Functional** — внешний IP через два HTTP endpoint'а; VPN считается рабочим,
   только если результат соответствует allowlist страны/ASN/IP.
4. **Resources** — load average, CPU sing-box, VSZ, RSS, RAM, swap, температура,
   overlay-диск, сетевые счётчики.

Transport проверяется часто с gateway и не создаёт нагрузки на роутер. Runtime,
functional и resources собираются одной короткой SSH-сессией с интервалом,
настраиваемым в inventory. Две IP-проверки запускаются только в health-cycle,
а тяжёлые логи и `top` — вручную или после аномалии.

## Данные

PostgreSQL хранит роутеры, gateway bindings, raw samples, агрегаты, события,
инциденты, команды и audit log. Prometheus хранит time-series метрики для
графиков и правил alerting. PostgreSQL остаётся источником состояния бота и
операционных событий; Prometheus — источником временных рядов.

## Адаптивный baseline

Baseline строится отдельно для каждого роутера, модели и временного окна.
Сохраняются median, P95, MAD, min/max и число наблюдений для load, CPU, VSZ,
RSS, RAM, температуры и сетевого трафика. После ручного изменения конфигурации
создаётся `config_change` event, старый профиль сохраняется, а новый проходит
период обучения. В baseline попадают только измерения с рабочим внешним IP.

Alert требует нескольких последовательных отклонений и cooldown. Для VSZ
отдельно проверяются абсолютное отклонение, рост и сочетание с RSS.

## Безопасность

- Секреты только через environment/secrets, не в inventory и коде.
- SSH-команды allowlist'ятся на стороне monitor; произвольный shell из Telegram
  запрещён.
- Операции restart/reconnect требуют подтверждения и записываются в audit log.
- Реальные серверы не изменяются этим репозиторием без отдельного deployment
  подтверждения.

## Начальный inventory

Inventory будет содержать router id/name, модель, gateway host, reverse port,
ожидаемые egress policy и расписание. Текущие bindings:

| Router | Port | Gateway |
|---|---:|---|
| gribanov-apartments163 | 2201 | aeza.balaganet.site |
| gribanov-apartments189 | 2202 | gateway-a |
| Tatarskaya-1-7 | 2203 | irving.balaganet.site |
| gribanov-telek | 2204 | gateway-a |
| gribanov-podval | 2205 | gateway-a |
