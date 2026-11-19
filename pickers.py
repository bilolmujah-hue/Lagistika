"""Guruh tanlash oynasi (umumiy): scope "p" - admin pool akkaunti guruhlari, "a" - foydalanuvchi avto xabar guruhlari."""
import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import core
import database as dbm
import userbot

PAGE = 10


def selected_ids(uid, scope, aid) -> set:
    if scope == "p":
        return {g["chat_id"] for g in dbm.pool_groups(aid)}
    camp = dbm.ap_ensure(uid)
    return {g["chat_id"] for g in dbm.ap_groups(camp["id"])}


async def show(msg: Message, uid: int, scope: str, aid: int, page: int, refresh=False, edit=False):
    if not userbot.is_connected(aid):
        await msg.answer("❌ Akkaunt ulanmagan.")
        return
    if refresh or aid not in userbot.GROUP_CACHE:
        try:
            await userbot.list_groups(aid)
        except Exception:
            logging.exception("Guruhlar ro'yxati")
            await msg.answer("❌ Guruhlarni o'qib bo'lmadi.")
            return
    groups = userbot.GROUP_CACHE.get(aid, [])
    if not groups:
        await msg.answer("Bu akkauntda guruh topilmadi. Avval kerakli guruhlarga a'zo bo'ling.")
        return
    on = selected_ids(uid, scope, aid)
    bad = dbm.ap_bad_ids() if scope == "a" else {}
    pages = (len(groups) - 1) // PAGE + 1
    page = max(0, min(page, pages - 1))
    def mark(g):
        if g["chat_id"] in on:
            return "✅"
        if scope == "a" and (not g.get("can_write", True) or g["chat_id"] in bad):
            return "🚫"
        return "▫️"
    rows = [[InlineKeyboardButton(text=f"{mark(g)} {g['title']}"[:60],
                                  callback_data=f"gp:{scope}:{aid}:{g['chat_id']}:{page}")]
            for g in groups[page * PAGE:(page + 1) * PAGE]]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"gpp:{scope}:{aid}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"gpp:{scope}:{aid}:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="☑️ Hammasi", callback_data=f"gpa:{scope}:{aid}:{page}"),
                 InlineKeyboardButton(text="🧹 Tozalash", callback_data=f"gpc:{scope}:{aid}:{page}")])
    rows.append([InlineKeyboardButton(text="✔️ Tayyor", callback_data=f"gpd:{scope}:{aid}")])
    what = "yuk e'lonlari yoziladigan" if scope == "p" else "avto xabar yuboriladigan"
    limit = f" (eng ko'pi {config.AP_MAX_GROUPS} ta)" if scope == "a" else ""
    text = f"📡 {what.capitalize()} guruhlarni tanlang{limit}.\n✅ = tanlangan. Tanlangan: {len(on)} ta"
    if scope == "a":
        text += ("\n🚫 = bu guruhga yozib bo'lmaydi yoki u xabarlarni o'chiradi (spam xavfi) - tanlab bo'lmaydi.")
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        await core.safe_edit(msg, text, kb)
    else:
        await msg.answer(text, reply_markup=kb)


def blocked_reason(chat_id, aid):
    g = next((x for x in userbot.GROUP_CACHE.get(aid, []) if x["chat_id"] == chat_id), None)
    if g and not g.get("can_write", True):
        return g.get("why") or "yozib bo'lmaydi"
    return dbm.ap_bad_ids().get(chat_id)


def toggle(uid, scope, aid, chat_id) -> str:
    title = next((g["title"] for g in userbot.GROUP_CACHE.get(aid, []) if g["chat_id"] == chat_id), str(chat_id))
    on = chat_id in selected_ids(uid, scope, aid)
    if scope == "a" and not on:
        why = blocked_reason(chat_id, aid)
        if why:
            return f"🚫 Qo'shib bo'lmaydi: {why}"
    if scope == "p":
        dbm.pool_set_group(aid, chat_id, title, not on)
    else:
        camp = dbm.ap_ensure(uid)
        if not on and len(dbm.ap_groups(camp["id"])) >= config.AP_MAX_GROUPS:
            return f"Eng ko'pi {config.AP_MAX_GROUPS} ta guruh"
        dbm.ap_set_group(camp["id"], chat_id, title, not on)
    return "Olib tashlandi" if on else "Qo'shildi"


def select_all(uid, scope, aid):
    groups = userbot.GROUP_CACHE.get(aid, [])
    if scope == "p":
        for g in groups:
            dbm.pool_set_group(aid, g["chat_id"], g["title"], True)
    else:
        camp = dbm.ap_ensure(uid)
        have = {g["chat_id"] for g in dbm.ap_groups(camp["id"])}
        for g in groups:
            if len(have) >= config.AP_MAX_GROUPS:
                break
            if blocked_reason(g["chat_id"], aid):
                continue
            if g["chat_id"] not in have:
                dbm.ap_set_group(camp["id"], g["chat_id"], g["title"], True)
                have.add(g["chat_id"])


def clear(uid, scope, aid):
    if scope == "p":
        dbm.pool_clear_groups(aid)
    else:
        dbm.ap_clear_groups(dbm.ap_ensure(uid)["id"])
