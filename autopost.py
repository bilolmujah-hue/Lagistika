"""AVTO XABAR: foydalanuvchining shaxsiy akkaunti orqali tanlangan guruhlarga e'lonni muntazam yuborish.

- Interval: har 5/10/15/30/60 daqiqada (foydalanuvchi tanlaydi).
- Har bir guruhga kuniga eng ko'pi AP_MAX_PER_GROUP_DAY (20) ta. Limit tugasa ertasi kuni avtomatik davom etadi.
- Guruh yozishni taqiqlasa / ban qilsa - shu guruh avtomatik o'chiriladi va foydalanuvchi ogohlantiriladi.
- Telegram cheklov qo'ysa (FloodWait/PeerFlood) - kampaniya pauza qilinadi (akkaunt ban bo'lmasligi uchun).
- Obuna tugasa kampaniya to'xtaydi.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup as K
from telethon import errors

import config
import core
import database as dbm
import userbot

_RUNNING: dict = {}
_NOTICED: set = set()

BAN_ERRORS = {"ChatWriteForbiddenError", "UserBannedInChannelError", "ChannelPrivateError",
              "ChatAdminRequiredError", "ChatRestrictedError", "ChatGuestSendForbiddenError",
              "ChatSendPlainForbiddenError", "ChannelInvalidError", "PeerIdInvalidError",
              "ChatIdInvalidError", "UserNotParticipantError", "ChatSendMediaForbiddenError"}


def _now():
    return datetime.now(config.TZ)


async def _notice_once(uid, key, text):
    k = (uid, key, dbm.today().isoformat())
    if k in _NOTICED:
        return
    _NOTICED.add(k)
    await core.send(uid, text)


# ====================================================================== DVIGATEL

def _due(c) -> bool:
    pu = dbm._dt(c.get("paused_until"))
    if pu and pu > _now():
        return False
    last = dbm._dt(c.get("last_run"))
    return not last or (_now() - last) >= timedelta(minutes=max(config.AP_INTERVALS[0], c["interval_min"] or 5))


async def loop():
    while True:
        try:
            for c in dbm.ap_active():
                t = _RUNNING.get(c["id"])
                if (t and not t.done()) or not _due(c):
                    continue
                _RUNNING[c["id"]] = asyncio.create_task(run_once(c))
        except Exception:
            logging.exception("Avto xabar sikli xatosi")
        await asyncio.sleep(20)


async def _send(cl, chat_id, text):
    try:
        return await cl.send_message(chat_id, text)
    except ValueError:
        await cl.get_dialogs(limit=500)  # entity keshda yo'q - dialoglarni yuklab qayta urinamiz
        return await cl.send_message(chat_id, text)


async def run_once(c):
    uid = c["user_id"]
    try:
        u = dbm.get_user(uid)
        if not dbm.has_access(u):
            dbm.ap_update(uid, active=0)
            await core.send(uid, "⏸ Obunangiz tugagani uchun «Avto xabar» to'xtatildi. To'lovdan keyin qayta yoqing.")
            return
        acc, cl = userbot.user_client(uid)
        if not cl:
            dbm.ap_update(uid, last_run=dbm.now_iso())
            await _notice_once(uid, "noacc", "⚠️ «Avto xabar»: akkauntingiz ulanmagan yoki sessiya tugagan. "
                                             "«📢 Avto xabar» bo'limidan qayta ulang.")
            return
        groups = dbm.ap_groups(c["id"], active_only=True)
        if not c.get("text") or not groups:
            dbm.ap_update(uid, active=0)
            await core.send(uid, "⏸ «Avto xabar» to'xtatildi: matn yoki faol guruh yo'q.")
            return
        dbm.ap_update(uid, last_run=dbm.now_iso())
        day = dbm.today().isoformat()
        cap = config.AP_MAX_PER_GROUP_DAY
        capped = 0
        for g in groups:
            if dbm.ap_count(c["id"], g["chat_id"], day) >= cap:
                capped += 1
                continue
            try:
                await _send(cl, g["chat_id"], c["text"])
                dbm.ap_inc(c["id"], g["chat_id"], day)
            except errors.SlowModeWaitError:
                pass  # guruhda sekin rejim - keyingi aylanishda
            except errors.FloodWaitError as e:
                until = (_now() + timedelta(seconds=e.seconds + 60)).isoformat(timespec="seconds")
                dbm.ap_update(uid, paused_until=until)
                await core.send(uid, f"⏳ Telegram vaqtincha cheklov qo'ydi. Avto xabar {e.seconds // 60 + 1} daqiqaga "
                                     "pauza qilindi (akkauntingiz xavfsizligi uchun).")
                break
            except errors.PeerFloodError:
                until = (_now() + timedelta(hours=24)).isoformat(timespec="seconds")
                dbm.ap_update(uid, paused_until=until)
                await core.send(uid, "⛔ Telegram akkauntingizga spam cheklovi qo'ydi. Avto xabar 24 soatga "
                                     "to'xtatildi. Intervalni oshirish va guruhlar sonini kamaytirish tavsiya etiladi.")
                break
            except Exception as e:
                name = type(e).__name__
                if name in BAN_ERRORS or "FORBIDDEN" in str(e).upper() or "BANNED" in str(e).upper():
                    dbm.ap_disable_group(c["id"], g["chat_id"], name)
                    await core.send(uid, f"🚫 «{g['title']}» guruhiga yozib bo'lmadi (taqiqlangan yoki "
                                         "chiqarilgansiz). Bu guruh ro'yxatdan o'chirildi.")
                else:
                    logging.warning("Avto xabar xatosi (%s, %s): %s", uid, g["chat_id"], e)
            await asyncio.sleep(random.uniform(3, 7))
        if capped and capped == len(groups):
            await _notice_once(uid, "cap", f"✅ Bugungi limit tugadi (har guruhga {cap} ta). "
                                           "Ertaga avtomatik davom etadi.")
    except Exception:
        logging.exception("Avto xabar yuborishda xato (user %s)", uid)


# ====================================================================== BOSHQARUV

def start(uid) -> str | None:
    c = dbm.ap_ensure(uid)
    acc, cl = userbot.user_client(uid)
    if not cl:
        return "Avval akkauntingizni ulang."
    if not dbm.ap_groups(c["id"], active_only=True):
        return "Avval guruhlarni tanlang."
    if not (c.get("text") or "").strip():
        return "Avval yuboriladigan matnni yozing."
    dbm.ap_update(uid, active=1, paused_until=None, last_run=None)
    return None


def stop(uid):
    dbm.ap_update(uid, active=0)


def status(uid) -> dict:
    c = dbm.ap_ensure(uid)
    acc, cl = userbot.user_client(uid)
    groups = dbm.ap_groups(c["id"])
    day = dbm.today().isoformat()
    return {
        "akkaunt": ((acc["name"] or acc["phone"] or f"#{acc['id']}") if acc else None), "ulangan": bool(cl),
        "holat": "ishlamoqda" if c["active"] else "to'xtatilgan",
        "pauza_gacha": dbm.fmt_dt(c["paused_until"], True) if dbm._dt(c.get("paused_until")) and
        dbm._dt(c["paused_until"]) > _now() else None,
        "interval_daqiqa": c["interval_min"], "kunlik_limit_guruhga": config.AP_MAX_PER_GROUP_DAY,
        "matn": c.get("text"), "bugun_yuborildi": dbm.ap_sent_today(c["id"], day),
        "guruhlar": [{"nomi": g["title"], "faol": bool(g["active"]), "izoh": g["note"],
                      "bugun": dbm.ap_count(c["id"], g["chat_id"], day)} for g in groups],
    }


def menu(uid):
    st = status(uid)
    c = dbm.ap_get(uid)
    lines = ["📢 Avto xabar\n"]
    if not st["akkaunt"]:
        lines.append("Bot sizning Telegram akkauntingiz orqali tanlangan guruhlarga e'loningizni "
                     f"muntazam yuboradi (har guruhga kuniga ko'pi bilan {config.AP_MAX_PER_GROUP_DAY} ta).\n\n"
                     "Boshlash uchun akkauntingizni ulang 👇")
        return "\n".join(lines), K(inline_keyboard=[[B(text="🔗 Akkaunt ulash", callback_data="ap:connect")]])
    active_groups = sum(1 for g in st["guruhlar"] if g["faol"])
    lines += [
        f"👤 Akkaunt: {st['akkaunt']} {'✅' if st['ulangan'] else '❌ (ulanmagan)'}",
        f"{'▶️ Ishlamoqda' if c['active'] else '⏸ To' + chr(39) + 'xtatilgan'}"
        + (f" (pauza {st['pauza_gacha']} gacha)" if st["pauza_gacha"] else ""),
        f"⏱ Har {st['interval_daqiqa']} daqiqada · har guruhga kuniga ≤{st['kunlik_limit_guruhga']} ta",
        f"📡 Guruhlar: {len(st['guruhlar'])} ta (faol {active_groups})",
        f"📤 Bugun yuborildi: {st['bugun_yuborildi']} ta",
        "",
        "✏️ Matn: " + (f"\n{st['matn'][:600]}" if st["matn"] else "kiritilmagan"),
    ]
    rows = [
        [B(text="📡 Guruhlar", callback_data="ap:groups"), B(text="✏️ Matn", callback_data="ap:text")],
        [B(text=f"⏱ Interval: {st['interval_daqiqa']} daq", callback_data="ap:int"),
         B(text="📊 Hisobot", callback_data="ap:stat")],
        [B(text="⏸ To'xtatish", callback_data="ap:stop") if c["active"]
         else B(text="▶️ Boshlash", callback_data="ap:start")],
        [B(text="🔌 Akkauntni uzish", callback_data="ap:off")],
    ]
    if not st["ulangan"]:
        rows.insert(0, [B(text="🔗 Qayta ulash", callback_data="ap:connect")])
    return "\n".join(lines), K(inline_keyboard=rows)


def interval_kb():
    return K(inline_keyboard=[[B(text=f"{n} daq", callback_data=f"ap:iv:{n}") for n in config.AP_INTERVALS],
                              [B(text="🔙 Orqaga", callback_data="ap:menu")]])


def report_text(uid) -> str:
    st = status(uid)
    lines = [f"📊 Avto xabar hisoboti ({dbm.today().strftime('%d.%m.%Y')})\n"]
    for g in st["guruhlar"]:
        mark = "✅" if g["faol"] else "🚫"
        extra = f" - o'chirilgan ({g['izoh']})" if not g["faol"] else ""
        lines.append(f"{mark} {g['nomi']}: {g['bugun']}/{st['kunlik_limit_guruhga']}{extra}")
    if not st["guruhlar"]:
        lines.append("Guruh tanlanmagan.")
    lines.append(f"\nJami bugun: {st['bugun_yuborildi']} ta")
    return "\n".join(lines)
