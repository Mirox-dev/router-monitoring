from __future__ import annotations

import asyncio
import os

import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Все роутеры", callback_data="routers")],
        [InlineKeyboardButton(text="События", callback_data="events"), InlineKeyboardButton(text="Обновить", callback_data="routers")],
    ])


def is_admin(user_id: int) -> bool:
    allowed = {item.strip() for item in os.environ.get("ROUTER_MONITOR_ADMIN_IDS", "").split(",") if item.strip()}
    return str(user_id) in allowed


def router_menu(router_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Проверить сейчас", callback_data=f"check:{router_id}")],
        [InlineKeyboardButton(text="Перезапустить sing-box", callback_data=f"restart:{router_id}")],
        [InlineKeyboardButton(text="Назад", callback_data="routers")],
    ])


async def status(pool: asyncpg.Pool) -> str:
    rows = await pool.fetch("""SELECT r.display_name, h.transport_ok, h.process_ok, h.tun_ok, h.foreign_ip_ok, h.load1, h.mem_available_kb, h.vsz_kb, h.rss_kb, h.collected_at FROM routers r LEFT JOIN LATERAL (SELECT * FROM health_samples WHERE router_id=r.id ORDER BY collected_at DESC LIMIT 1) h ON true ORDER BY r.id""")
    if not rows:
        return "Роутеры ещё не зарегистрированы."
    lines = ["Состояние роутеров:"]
    for row in rows:
        state = "✅" if row["transport_ok"] and row["foreign_ip_ok"] else "🔴"
        lines.append(f"{state} {row['display_name']} · process={'ok' if row['process_ok'] else 'down'} · tun={'ok' if row['tun_ok'] else 'down'} · IP={'foreign' if row['foreign_ip_ok'] else 'not foreign'} · load={row['load1'] or '—'} · RAM={row['mem_available_kb'] or '—'} KiB · VSZ={row['vsz_kb'] or '—'} KiB")
    return "\n".join(lines)


async def router_list_menu(pool: asyncpg.Pool) -> InlineKeyboardMarkup:
    rows = await pool.fetch("SELECT id, display_name FROM routers ORDER BY id")
    buttons = [[InlineKeyboardButton(text=row["display_name"], callback_data=f"router:{row['id']}")] for row in rows]
    buttons.append([InlineKeyboardButton(text="Обновить", callback_data="routers")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def main() -> None:
    token = os.environ["ROUTER_MONITOR_BOT_TOKEN"]
    dsn = os.environ["ROUTER_MONITOR_DATABASE_URL"]
    alert_chat_id = os.environ["ROUTER_MONITOR_ALERT_CHAT_ID"]
    pool = await asyncpg.create_pool(dsn)
    bot, dispatcher = Bot(token), Dispatcher()

    @dispatcher.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer("Центральный мониторинг роутеров", reply_markup=menu())

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
        await callback.message.edit_text(await status(pool), reply_markup=await router_list_menu(pool))
        await callback.answer()

    @dispatcher.callback_query(F.data.startswith("router:"))
    async def router_detail(callback: CallbackQuery) -> None:
        router_id = callback.data.split(":", 1)[1]
        row = await pool.fetchrow("""SELECT r.display_name, h.* FROM routers r LEFT JOIN LATERAL (SELECT * FROM health_samples WHERE router_id=r.id ORDER BY collected_at DESC LIMIT 1) h ON true WHERE r.id=$1""", router_id)
        if not row:
            await callback.answer("Роутер не найден", show_alert=True)
            return
        text = f"{row['display_name']}\nprocess: {'ok' if row['process_ok'] else 'down'}\nservice: {'ok' if row['service_ok'] else 'down'}\ntun0: {'ok' if row['tun_ok'] else 'down'}\nforeign IP: {'ok' if row['foreign_ip_ok'] else 'down'}\nload: {row['load1'] or '—'}\nRAM: {row['mem_available_kb'] or '—'} KiB\nVSZ/RSS: {row['vsz_kb'] or '—'}/{row['rss_kb'] or '—'} KiB"
        await callback.message.edit_text(text, reply_markup=router_menu(router_id))
        await callback.answer()

    @dispatcher.callback_query(F.data.startswith("check:"))
    async def check_now(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        router_id = callback.data.split(":", 1)[1]
        await pool.execute("INSERT INTO monitor_commands (router_id, action, requested_by) VALUES ($1, 'health_check', $2)", router_id, str(callback.from_user.id))
        await callback.answer("Проверка поставлена в очередь")

    @dispatcher.callback_query(F.data.startswith("restart:"))
    async def restart_prompt(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        router_id = callback.data.split(":", 1)[1]
        confirm = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подтвердить restart", callback_data=f"restart-confirm:{router_id}")], [InlineKeyboardButton(text="Отмена", callback_data="routers")]])
        await callback.message.edit_text("Команда перезапустит sing-box на роутере. Подтвердить?", reply_markup=confirm)
        await callback.answer()

    @dispatcher.callback_query(F.data.startswith("restart-confirm:"))
    async def restart_confirm(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        router_id = callback.data.split(":", 1)[1]
        await pool.execute("INSERT INTO monitor_commands (router_id, action, requested_by) VALUES ($1, 'restart_sing_box', $2)", router_id, str(callback.from_user.id))
        await callback.message.edit_text("Перезапуск поставлен в очередь.", reply_markup=menu())
        await callback.answer()

    @dispatcher.callback_query(F.data == "events")
    async def events(callback: CallbackQuery) -> None:
        rows = await pool.fetch("SELECT router_id, kind, severity, created_at FROM monitor_events ORDER BY created_at DESC LIMIT 10")
        text = "\n".join(f"{row['created_at']:%H:%M} {row['severity']} {row['router_id']}: {row['kind']}" for row in rows) or "Событий нет."
        await callback.message.edit_text(text, reply_markup=menu())
        await callback.answer()

    try:
        await dispatcher.start_polling(bot)
    finally:
        await runner.cleanup()
        await pool.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
