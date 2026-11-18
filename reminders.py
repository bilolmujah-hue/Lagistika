"""Avtomatik eslatmalar: hujjat muddatlari, sanali vazifalar va obuna tugashi.
Har 30 daqiqada tekshiradi, foydalanuvchiga kuniga bir marta (NOTIFY_HOUR dan keyin) yuboradi.

Hujjat va fura eslatmalari MENEJERGA ham, tegishli HAYDOVCHIGA ham boradi:
 - haydovchi hujjati -> shu haydovchiga;
 - fura hujjati / fura eslatmasi -> furaga biriktirilgan haydovchiga.
"""
import asyncio
import logging
from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import core
import database as dbm


def _doc_key(r, prefix="doc") -> str:
    d = r["days_left"]
    if d < 0:
        return f"{prefix}:{r['id']}:{r['expiry_date']}:over:{(-d - 1) // 3}"
    t = min(x for x in config.DOC_NOTIFY_DAYS if d <= x)
    return f"{prefix}:{r['id']}:{r['expiry_date']}:{t}"


def _doc_line(r) -> str:
    d = r["days_left"]
    if d < 0:
        status, emoji = f"MUDDATI O'TGAN ({-d} kun oldin)", "🔴"
    elif d == 0:
        status, emoji = "muddati BUGUN tugaydi", "🔴"
    else:
        status = f"{d} kun qoldi"
        emoji = "🔴" if d <= config.RED_DAYS else "🟡"
    return f"{emoji} {r['owner_icon']} {r['owner_name']} - {r['doc_label']}\n    {dbm.fmt_date(r['expiry_date'])} ({status})"


async def _send_chunks(uid, title, lines):
    chunk, size = [], 0
    for line in lines:
        if size + len(line) > 3500:
            await core.bot.send_message(uid, title + "\n\n" + "\n\n".join(chunk))
            chunk, size = [], 0
        chunk.append(line)
        size += len(line)
    if chunk:
        await core.bot.send_message(uid, title + "\n\n" + "\n\n".join(chunk))


def _doc_recipient_driver(mid, r, vehicle_driver):
    """Hujjat qaysi haydovchi foydalanuvchiga tegishli (Telegram user_id) yoki None."""
    if r["owner_type"] == "driver":
        d = dbm.get_driver(mid, r["owner_id"])
        return d.get("tg_user_id") if d else None
    return vehicle_driver.get(r["owner_id"])


async def _notify_docs(mid: int):
    rows = dbm.expiring(mid, within_days=max(config.DOC_NOTIFY_DAYS))
    if not rows:
        return
    vehicle_driver = {v["id"]: dbm.driver_user_of_vehicle(mid, v["id"]) for v in dbm.list_vehicles(mid)}
    m_keys, m_lines = [], []
    per_driver: dict = {}
    for r in rows:
        key = _doc_key(r)
        if not dbm.was_sent(key):
            m_keys.append(key)
            m_lines.append(_doc_line(r))
        du = _doc_recipient_driver(mid, r, vehicle_driver)
        if du:
            dkey = _doc_key(r, f"docd{du}")
            if not dbm.was_sent(dkey):
                e = per_driver.setdefault(du, ([], []))
                e[0].append(dkey)
                e[1].append(_doc_line(r))
    if m_lines:
        await _send_chunks(mid, "⏰ Hujjat muddati eslatmasi", m_lines)
        for k in m_keys:
            dbm.mark_sent(k)
    for du, (keys, lines) in per_driver.items():
        try:
            await _send_chunks(du, "⏰ Hujjatlaringiz muddati eslatmasi", lines)
            for k in keys:
                dbm.mark_sent(k)
        except Exception:
            logging.warning("Haydovchiga eslatma yuborilmadi (%s)", du)


async def _notify_reminders(mid: int, today):
    for r in dbm.due_reminders(mid, today):
        late = (today - datetime.fromisoformat(r["remind_date"]).date()).days
        text = f"🔔 Eslatma: {dbm.reminder_title(r)}\n"
        if r.get("plate"):
            text += f"🚛 Fura: {r['plate']}\n"
        text += f"📅 Sana: {dbm.fmt_date(r['remind_date'])}"
        if late > 0:
            text += f" ({late} kun kechikdi)"
        key = f"rem:{r['id']}:{today.isoformat()}"
        if not dbm.was_sent(key):
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"rd:{r['id']}")]])
            await core.bot.send_message(mid, text, reply_markup=kb)
            dbm.mark_sent(key)
        if r.get("vehicle_id"):
            du = dbm.driver_user_of_vehicle(mid, r["vehicle_id"])
            dkey = f"remd:{r['id']}:{du}:{today.isoformat()}"
            if du and not dbm.was_sent(dkey):
                try:
                    await core.bot.send_message(du, text)
                    dbm.mark_sent(dkey)
                except Exception:
                    logging.warning("Haydovchiga eslatma yuborilmadi (%s)", du)


async def _notify_subscription(u, today):
    st, until = dbm.sub_state(u)
    if st not in ("trial", "paid", "expired") or not until:
        return
    days = (until.astimezone(config.TZ).date() - today).days
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 To'lov qilish",
                                                                     callback_data="pay:info")]])
    what = "Bepul sinov muddatingiz" if st == "trial" or (st == "expired" and not u.get("paid_until")) else "Obunangiz"
    if st in ("trial", "paid") and days in (3, 1, 0):
        key = f"sub:{u['user_id']}:{until.date()}:{days}"
        if not dbm.was_sent(key):
            when = "bugun" if days == 0 else f"{days} kundan keyin"
            await core.bot.send_message(u["user_id"], f"⏳ {what} {when} ({dbm.fmt_dt(until, True)}) tugaydi.\n"
                                                      "Botdan uzluksiz foydalanish uchun obunani uzaytiring.",
                                        reply_markup=kb)
            dbm.mark_sent(key)
    elif st == "expired" and -7 <= days < 0:
        key = f"subx:{u['user_id']}:{until.date()}:{1 if days == -1 else (7 if days == -7 else 0)}"
        if days in (-1, -7) and not dbm.was_sent(key):
            await core.bot.send_message(u["user_id"], f"⛔ {what} tugadi. Hujjat eslatmalari, GPS, yuk qidirish va "
                                                      "avto xabar to'xtatilgan. To'lov qilib davom ettiring 👇",
                                        reply_markup=kb)
            dbm.mark_sent(key)


async def check_and_notify():
    now = datetime.now(config.TZ)
    if now.hour < config.NOTIFY_HOUR:
        return
    today = now.date()
    for mid in dbm.manager_ids():
        try:
            u = dbm.get_user(mid)
            await _notify_subscription(u, today)
            if not dbm.has_access(u):
                continue
            await _notify_docs(mid)
            await _notify_reminders(mid, today)
        except Exception:
            logging.exception("Eslatma yuborishda xato (user %s)", mid)


async def reminder_loop():
    while True:
        try:
            await check_and_notify()
        except Exception:
            logging.exception("Eslatma sikli xatosi")
        await asyncio.sleep(30 * 60)
