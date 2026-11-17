"""Avtomatik eslatmalar: hujjat muddatlari va sanali vazifalar.
Har 30 daqiqada tekshiradi, lekin foydalanuvchiga kuniga bir marta (NOTIFY_HOUR dan keyin) yuboradi."""
import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import database as dbm


def _doc_key(r) -> str:
    """Bir hujjat uchun bir xil eslatma ikki marta yuborilmasligi uchun kalit."""
    d = r["days_left"]
    if d < 0:
        # muddati o'tgan: birinchi kuni, keyin har 3 kunda
        return f"doc:{r['id']}:{r['expiry_date']}:over:{(-d - 1) // 3}"
    # muddati yaqinlashgan: shu kun uchun mos eng kichik chegara (30, 15, 7, 3, 1, 0)
    t = min(x for x in config.DOC_NOTIFY_DAYS if d <= x)
    return f"doc:{r['id']}:{r['expiry_date']}:{t}"


def _doc_line(r) -> str:
    d = r["days_left"]
    if d < 0:
        status = f"MUDDATI O'TGAN ({-d} kun oldin)"
        emoji = "🔴"
    elif d == 0:
        status, emoji = "muddati BUGUN tugaydi", "🔴"
    else:
        status = f"{d} kun qoldi"
        emoji = "🔴" if d <= config.RED_DAYS else "🟡"
    return f"{emoji} {r['owner_icon']} {r['owner_name']} - {r['doc_label']}\n    {dbm.fmt_date(r['expiry_date'])} ({status})"


async def _notify_docs(bot: Bot, uid: int):
    keys, lines = [], []
    for r in dbm.expiring(uid, within_days=max(config.DOC_NOTIFY_DAYS)):
        key = _doc_key(r)
        if dbm.was_sent(key):
            continue
        keys.append(key)
        lines.append(_doc_line(r))
    if not lines:
        return
    # Telegram xabar uzunligi chegarasi uchun bo'laklab yuboramiz
    chunk, size = [], 0
    for line in lines:
        if size + len(line) > 3500:
            await bot.send_message(uid, "⏰ Hujjat muddati eslatmasi\n\n" + "\n\n".join(chunk))
            chunk, size = [], 0
        chunk.append(line)
        size += len(line)
    if chunk:
        await bot.send_message(uid, "⏰ Hujjat muddati eslatmasi\n\n" + "\n\n".join(chunk))
    for k in keys:
        dbm.mark_sent(k)


async def _notify_reminders(bot: Bot, uid: int, today):
    for r in dbm.due_reminders(uid, today):
        key = f"rem:{r['id']}:{today.isoformat()}"
        if dbm.was_sent(key):
            continue
        late = (today - datetime.fromisoformat(r["remind_date"]).date()).days
        text = f"🔔 Eslatma: {dbm.reminder_title(r)}\n"
        if r.get("plate"):
            text += f"🚛 Fura: {r['plate']}\n"
        text += f"📅 Sana: {dbm.fmt_date(r['remind_date'])}"
        if late > 0:
            text += f" ({late} kun kechikdi)"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"rd:{r['id']}")]])
        await bot.send_message(uid, text, reply_markup=kb)
        dbm.mark_sent(key)


async def check_and_notify(bot: Bot):
    now = datetime.now(config.TZ)
    if now.hour < config.NOTIFY_HOUR:
        return
    today = now.date()
    for uid in dbm.all_user_ids():
        try:
            await _notify_docs(bot, uid)
            await _notify_reminders(bot, uid, today)
        except Exception:
            # masalan foydalanuvchi botni bloklagan bo'lsa - boshqalarga yuborishda davom etamiz
            logging.exception("Eslatma yuborishda xato (user %s)", uid)


async def reminder_loop(bot: Bot):
    while True:
        try:
            await check_and_notify(bot)
        except Exception:
            logging.exception("Eslatma sikli xatosi")
        await asyncio.sleep(30 * 60)
