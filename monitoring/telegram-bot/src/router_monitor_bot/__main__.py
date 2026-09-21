from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re

import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


class AddExternalIP(StatesGroup):
    waiting_for_ip = State()


last_screen_by_chat: dict[int, int] = {}


async def send_screen(bot: Bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None, *, old_message_id: int | None = None, user_message: Message | None = None) -> None:
    old_id = old_message_id or last_screen_by_chat.get(chat_id)
    if old_id and old_id != (user_message.message_id if user_message else None):
        try:
            await bot.delete_message(chat_id, old_id)
        except TelegramBadRequest:
            pass
    if user_message:
        try:
            await user_message.delete()
        except TelegramBadRequest:
            pass
    sent = await bot.send_message(chat_id, text, reply_markup=markup)
    last_screen_by_chat[chat_id] = sent.message_id


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Все роутеры", callback_data="routers")],
        [InlineKeyboardButton(text="Внешние IP", callback_data="external_ips")],
        [InlineKeyboardButton(text="События", callback_data="events"), InlineKeyboardButton(text="Обновить", callback_data="routers")],
    ])


def is_admin(user_id: int) -> bool:
    allowed = {item.strip() for item in os.environ.get("ROUTER_MONITOR_ADMIN_IDS", "").split(",") if item.strip()}
    return str(user_id) in allowed


def ssh_jump_chain(gateway_host: str) -> str:
    return f"root@193.124.129.211,root@{gateway_host}"


def router_menu(router_id: str, previous_id: str | None = None, next_id: str | None = None) -> InlineKeyboardMarkup:
    navigation = []
    if previous_id:
        navigation.append(InlineKeyboardButton(text="◀️ Предыдущий", callback_data=f"router:{previous_id}"))
    if next_id:
        navigation.append(InlineKeyboardButton(text="Следующий ▶️", callback_data=f"router:{next_id}"))
    rows = [
        [InlineKeyboardButton(text="Проверить сейчас", callback_data=f"check:{router_id}")],
        [InlineKeyboardButton(text="Перезапустить sing-box", callback_data=f"restart:{router_id}")],
    ]
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="◀️ Назад к роутерам", callback_data="routers")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def replace_menu(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Delete the old screen and send the next one."""
    await send_screen(callback.bot, callback.message.chat.id, text, markup, old_message_id=callback.message.message_id)


async def status(pool: asyncpg.Pool) -> str:
    rows = await pool.fetch("""SELECT r.display_name, r.gateway, r.reverse_port, h.transport_ok, h.process_ok, h.tun_ok, h.foreign_ip_ok, h.load1, h.mem_available_kb, h.vsz_kb, h.rss_kb, h.collected_at FROM routers r LEFT JOIN LATERAL (SELECT * FROM health_samples WHERE router_id=r.id ORDER BY collected_at DESC LIMIT 1) h ON true ORDER BY r.id""")
    if not rows:
        return "Роутеры ещё не зарегистрированы."
    lines = ["СОСТОЯНИЕ РОУТЕРОВ", ""]
    for row in rows:
        state = "🔴" if not row["transport_ok"] else ("✅" if row["foreign_ip_ok"] else "⚠️")
        gateway_match = re.search(r"\(([^)]+)\)$", row["gateway"] or "")
        gateway_host = gateway_match.group(1) if gateway_match else row["gateway"]
        if gateway_host == "host.docker.internal":
            gateway_host = "95.181.174.224"
        lines.extend([
            f"{state} {row['display_name']}",
            f"   SSH: {'подключён' if row['transport_ok'] else 'недоступен'}",
            f"   sing-box: {'работает' if row['process_ok'] else 'не работает'}",
            f"   ```ssh\n   ssh -J {ssh_jump_chain(gateway_host)} -p {row['reverse_port']} root@127.0.0.1\n   ```",
            "",
        ])
    return "\n".join(lines)


async def router_list_menu(pool: asyncpg.Pool) -> InlineKeyboardMarkup:
    rows = await pool.fetch("SELECT id, display_name FROM routers ORDER BY id")
    buttons = [[InlineKeyboardButton(text=row["display_name"], callback_data=f"router:{row['id']}")] for row in rows]
    buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="routers")])
    buttons.append([InlineKeyboardButton(text="◀️ В главное меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def external_ip_view(pool: asyncpg.Pool, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rows = await pool.fetch("SELECT value FROM external_ips ORDER BY value")
    lines = ["РАЗРЕШЁННЫЕ ВНЕШНИЕ IP", "", "Оба адреса от ifconfig.me и api.ipify.org должны входить в этот список.", ""]
    lines.extend(f"• {row['value']}" for row in rows)
    if not rows:
        lines.append("Список пуст.")
    buttons = []
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton(text="Добавить IP", callback_data="external_ip_add")])
        buttons.extend([[InlineKeyboardButton(text=f"Удалить {row['value']}", callback_data=f"external_ip_remove:{row['value']}")] for row in rows])
    buttons.append([InlineKeyboardButton(text="◀️ В главное меню", callback_data="menu")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def main() -> None:
    token = os.environ["ROUTER_MONITOR_BOT_TOKEN"]
    dsn = os.environ["ROUTER_MONITOR_DATABASE_URL"]
    alert_chat_id = os.environ["ROUTER_MONITOR_ALERT_CHAT_ID"]
    pool = await asyncpg.create_pool(dsn)
    bot, dispatcher = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)), Dispatcher()
    await bot.set_my_commands([
        BotCommand(command="start", description="Открыть главное меню"),
        BotCommand(command="routers", description="Показать роутеры"),
        BotCommand(command="external_ips", description="Разрешённые внешние IP"),
        BotCommand(command="events", description="Последние события"),
        BotCommand(command="cancel", description="Отменить ввод IP"),
    ])

    @dispatcher.message(CommandStart())
    async def start(message: Message) -> None:
        await send_screen(bot, message.chat.id, "Центральный мониторинг роутеров", menu(), user_message=message)

    @dispatcher.message(Command("routers"))
    async def routers_command(message: Message) -> None:
        await send_screen(bot, message.chat.id, await status(pool), await router_list_menu(pool), user_message=message)

    @dispatcher.message(Command("external_ips"))
    async def external_ips_command(message: Message) -> None:
        text, markup = await external_ip_view(pool, message.from_user.id)
        await send_screen(bot, message.chat.id, text, markup, user_message=message)

    @dispatcher.message(Command("events"))
    async def events_command(message: Message) -> None:
        rows = await pool.fetch("SELECT router_id, kind, severity, created_at FROM monitor_events ORDER BY created_at DESC LIMIT 10")
        text = "ПОСЛЕДНИЕ СОБЫТИЯ\n\n" + "\n".join(f"{row['created_at']:%H:%M} · {row['severity']} · {row['router_id']} · {row['kind']}" for row in rows) if rows else "Событий нет."
        await send_screen(bot, message.chat.id, text, menu(), user_message=message)

    @dispatcher.callback_query(F.data == "menu")
    async def main_menu(callback: CallbackQuery) -> None:
        await replace_menu(callback, "Центральный мониторинг роутеров", menu())
        await callback.answer()

    @dispatcher.callback_query(F.data == "external_ips")
    async def external_ips(callback: CallbackQuery) -> None:
        text, markup = await external_ip_view(pool, callback.from_user.id)
        await replace_menu(callback, text, markup)
        await callback.answer()

    @dispatcher.callback_query(F.data == "external_ip_add")
    async def external_ip_add(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await state.set_state(AddExternalIP.waiting_for_ip)
        await replace_menu(callback, "Отправьте IP одним сообщением, например: 8.8.8.8\n\nДля отмены нажмите /cancel")
        await callback.answer()

    @dispatcher.message(AddExternalIP.waiting_for_ip)
    async def external_ip_received(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            await state.clear()
            return
        value = (message.text or "").strip()
        if value.lower() == "/cancel":
            await state.clear()
            await send_screen(bot, message.chat.id, "Действие отменено.", menu(), user_message=message)
            return
        try:
            parsed = ipaddress.ip_address(value)
        except ValueError:
            await send_screen(bot, message.chat.id, "Это не корректный IP-адрес. Отправьте IPv4 или IPv6 ещё раз.", user_message=message)
            return
        await pool.execute("INSERT INTO external_ips(value) VALUES($1) ON CONFLICT (value) DO NOTHING", str(parsed))
        await state.clear()
        text, markup = await external_ip_view(pool, message.from_user.id)
        await send_screen(bot, message.chat.id, f"IP {parsed} добавлен.\n\n{text}", markup, user_message=message)

    @dispatcher.message(Command("cancel"))
    async def cancel_ip(message: Message, state: FSMContext) -> None:
        await state.clear()
        await send_screen(bot, message.chat.id, "Действие отменено.", menu(), user_message=message)

    @dispatcher.callback_query(F.data.startswith("external_ip_remove:"))
    async def external_ip_remove(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        value = callback.data.split(":", 1)[1]
        await pool.execute("DELETE FROM external_ips WHERE value=$1", value)
        text, markup = await external_ip_view(pool, callback.from_user.id)
        await replace_menu(callback, text, markup)
        await callback.answer("IP удалён")

    async def alert_webhook(request: web.Request) -> web.Response:
        payload = await request.json()
        lines = []
        for alert in payload.get("alerts", []):
            labels = alert.get("labels", {})
            state = "RESOLVED" if alert.get("status") == "resolved" else "FIRING"
            lines.append(f"{state}: {labels.get('alertname', 'router alert')} · {labels.get('router', labels.get('router_id', 'unknown'))}")
        if lines:
            await bot.send_message(alert_chat_id, "\n".join(lines))
        return web.json_response({"accepted": len(lines)})

    web_app = web.Application()
    web_app.router.add_post("/alerts", alert_webhook)
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8081).start()

    @dispatcher.callback_query(F.data == "routers")
    async def routers(callback: CallbackQuery) -> None:
        await replace_menu(callback, await status(pool), await router_list_menu(pool))
        await callback.answer()

    @dispatcher.callback_query(F.data.startswith("router:"))
    async def router_detail(callback: CallbackQuery) -> None:
        router_id = callback.data.split(":", 1)[1]
        row = await pool.fetchrow("""SELECT r.display_name, r.model, r.gateway, r.reverse_port, h.* FROM routers r LEFT JOIN LATERAL (SELECT * FROM health_samples WHERE router_id=r.id ORDER BY collected_at DESC LIMIT 1) h ON true WHERE r.id=$1""", router_id)
        if not row:
            await callback.answer("Роутер не найден", show_alert=True)
            return
        collected = row["collected_at"].strftime("%d.%m.%Y %H:%M:%S") if row["collected_at"] else "нет данных"
        checks_raw = row["checks"]
        checks = json.loads(checks_raw) if isinstance(checks_raw, str) else (checks_raw or {})
        ip1 = checks.get("ip1") or "нет данных"
        ip2 = checks.get("ip2") or "нет данных"
        text = (
            f"{row['display_name']}\n\n"
            f"МОДЕЛЬ\n"
            f"  {row['model']}\n\n"
            f"SSH-СОЕДИНЕНИЕ\n"
            f"  Сервер: {row['gateway']}\n"
            f"  Reverse SSH порт: {row['reverse_port']}\n"
            f"  Состояние: {'подключён' if row['transport_ok'] else 'недоступен'}\n\n"
            f"SING-BOX\n"
            f"  Процесс: {'работает' if row['process_ok'] else 'не работает'}\n"
            f"  Сервис init.d: {'активен' if row['service_ok'] else 'неактивен'}\n"
            f"  tun0: {'есть и поднят' if row['tun_ok'] else 'отсутствует или выключен'}\n"
            f"  IP через ifconfig.me: {ip1}\n"
            f"  IP через api.ipify.org: {ip2}\n"
            f"  Проверка allowlist: {'пройдена' if row['foreign_ip_ok'] else 'не пройдена'}\n\n"
            f"НАГРУЗКА\n"
            f"  Load average: {row['load1'] if row['load1'] is not None else '—'}\n"
            f"  CPU процесса: {row['cpu_percent'] if row['cpu_percent'] is not None else '—'} %\n"
            f"  RAM свободно: {row['mem_available_kb'] if row['mem_available_kb'] is not None else '—'} KiB\n"
            f"  Диск занят: {row['disk_used_percent'] if row['disk_used_percent'] is not None else '—'} %\n"
            f"  VSZ: {row['vsz_kb'] if row['vsz_kb'] is not None else '—'} KiB\n"
            f"  RSS: {row['rss_kb'] if row['rss_kb'] is not None else '—'} KiB\n"
            f"  Рестарты sing-box: {row['restart_count'] if row['restart_count'] is not None else '—'}\n\n"
            f"Последняя проверка: {collected}"
        )
        router_ids = [row["id"] for row in await pool.fetch("SELECT id FROM routers ORDER BY id")]
        position = router_ids.index(router_id)
        previous_id = router_ids[position - 1] if position > 0 else None
        next_id = router_ids[position + 1] if position + 1 < len(router_ids) else None
        await replace_menu(callback, text, router_menu(router_id, previous_id, next_id))
        await callback.answer()

    @dispatcher.callback_query(F.data.startswith("check:"))
    async def check_now(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        router_id = callback.data.split(":", 1)[1]
        await pool.execute("INSERT INTO monitor_commands (router_id, action, requested_by, status) VALUES ($1, 'health_check', $2, 'pending')", router_id, str(callback.from_user.id))
        await callback.answer("Проверка поставлена в очередь")

    @dispatcher.callback_query(F.data.startswith("restart:"))
    async def restart_prompt(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        router_id = callback.data.split(":", 1)[1]
        confirm = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подтвердить restart", callback_data=f"restart-confirm:{router_id}")], [InlineKeyboardButton(text="Отмена", callback_data="routers")]])
        await replace_menu(callback, "Команда перезапустит sing-box на роутере. Подтвердить?", confirm)
        await callback.answer()

    @dispatcher.callback_query(F.data.startswith("restart-confirm:"))
    async def restart_confirm(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        router_id = callback.data.split(":", 1)[1]
        await pool.execute("INSERT INTO monitor_commands (router_id, action, requested_by, status) VALUES ($1, 'restart_sing_box', $2, 'pending')", router_id, str(callback.from_user.id))
        await replace_menu(callback, "Перезапуск поставлен в очередь.", menu())
        await callback.answer()

    @dispatcher.callback_query(F.data == "events")
    async def events(callback: CallbackQuery) -> None:
        rows = await pool.fetch("SELECT router_id, kind, severity, created_at FROM monitor_events ORDER BY created_at DESC LIMIT 10")
        text = "ПОСЛЕДНИЕ СОБЫТИЯ\n\n" + "\n".join(f"{row['created_at']:%H:%M} · {row['severity']} · {row['router_id']} · {row['kind']}" for row in rows) if rows else "Событий нет."
        await replace_menu(callback, text, menu())
        await callback.answer()

    try:
        await dispatcher.start_polling(bot)
    finally:
        await runner.cleanup()
        await pool.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
