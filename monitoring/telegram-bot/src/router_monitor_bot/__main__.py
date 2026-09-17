from __future__ import annotations

import asyncio
import os

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Все роутеры", callback_data="routers")],
        [InlineKeyboardButton(text="События", callback_data="events"), InlineKeyboardButton(text="Обновить", callback_data="routers")],
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


async def main() -> None:
    token = os.environ["ROUTER_MONITOR_BOT_TOKEN"]
    dsn = os.environ["ROUTER_MONITOR_DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn)
    bot, dispatcher = Bot(token), Dispatcher()

    @dispatcher.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer("Центральный мониторинг роутеров", reply_markup=menu())

    @dispatcher.callback_query(F.data == "routers")
    async def routers(callback: CallbackQuery) -> None:
        await callback.message.edit_text(await status(pool), reply_markup=menu())
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
        await pool.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
