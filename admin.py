"""ADMIN PANEL. Faqat adminlar ko'radi (bosh admin - .env dagi ALLOWED_USERS, qolganlari panel orqali qo'shiladi).

Bo'limlar: statistika, foydalanuvchilar (qidirish, bloklash, VIP, obuna uzaytirish, xabar yozish, GPS token),
to'lovlar (chek tekshiruvi, kartaga tushumlar), avto to'lov sozlamalari (karta, narx, muddat, to'lov akkaunti),
userbotlar (yuk qidirish akkauntlari pool i), GPS so'rovlari, VIP ro'yxati, adminlar, ommaviy xabar, Excel eksport.
"""
import asyncio
import io
import json
import logging

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup as K
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

import config
import core
import database as dbm
import gps
import llm
import login
import payments
import pickers
import userbot

router = Router()
PAGE = 8

FILTERS = [("all", "Hammasi"), ("managers", "Menejer"), ("drivers", "Haydovchi"), ("paid", "To'lagan"),
           ("trial", "Sinov"), ("expired", "Tugagan"), ("vip", "VIP"), ("blocked", "Blok"), ("noreg", "Ro'yxatsiz")]
STATE_UZ = {"admin": "👑 admin", "vip": "⭐ VIP", "paid": "✅ to'langan", "trial": "🆓 sinov",
            "expired": "⛔ tugagan", "blocked": "🚫 bloklangan", "driver": "🚛 haydovchi (faol)",
            "manager_expired": "🚛 haydovchi (menejer obunasi tugagan)", "unlinked": "🚛 haydovchi (ulanmagan)",
            "noreg": "❔ ro'yxatdan o'tmagan"}
SETTING_TITLES = {
    "card_number": ("💳 Karta raqami", "16 xonali karta raqamini yuboring"),
    "card_holder": ("👤 Karta egasi", "Karta egasining ism familiyasini yuboring (kartadagidek)"),
    "price": ("💰 Narx (so'm)", "1 davr narxini so'mda yuboring, masalan 100000"),
    "period_days": ("📅 Muddat (kun)", "1 to'lov necha kun beradi? Masalan 30"),
    "trial_days": ("🆓 Sinov (kun)", "Yangi menejerlarga necha kun bepul? Masalan 10"),
    "pay_window_min": ("⏳ To'lov vaqti (daq)", "To'lov uchun necha daqiqa beriladi? Masalan 10"),
    "user_dm_day": ("💬 Kunlik DM limiti", "Bitta foydalanuvchi uchun kuniga eng ko'pi nechta e'lon egasiga yoziladi?"),
    "search_results": ("🔎 Qidiruv natijalari", "Bitta qidiruvda nechta e'lon topilsa to'xtasin? (tavsiya: 5)"),
    "search_hours": ("🕒 Qidiruv soatlari", "Guruhlarda necha soat orqaga qidirilsin? (tavsiya: 12)"),
    "support": ("🆘 Yordam @username", "Foydalanuvchilar murojaat qiladigan @username yuboring"),
}
NUMERIC = {"price", "period_days", "trial_days", "pay_window_min", "user_dm_day", "search_results", "search_hours"}


def _uname(u) -> str:
    n = u.get("name") or "-"
    return n + (f" (@{u['username']})" if u.get("username") else "")


def back(to="ad:home", text="🔙 Orqaga"):
    return [B(text=text, callback_data=to)]


# ====================================================================== BOSH SAHIFA

def home_view(uid):
    s = dbm.global_stats()
    rev = len(dbm.payments_by_status("review"))
    gpsq = len(dbm.gps_req_pending())
    pool = dbm.acc_list("pool")
    on = sum(1 for a in pool if userbot.is_connected(a["id"]))
    pa, pc = userbot.pay_client()
    text = (f"🛠 ADMIN PANEL\n\n"
            f"👥 Foydalanuvchilar: {s['users']} (bugun +{s['new_today']}, faol bugun {s['active_today']})\n"
            f"✅ To'lagan: {s['paid']} · 🆓 Sinov: {s['trial']} · ⛔ Tugagan: {s['expired']} · ⭐ VIP: {s['vip']}\n"
            f"💰 Bugun: {core.money(s['rev_today'])} so'm · Bu oy: {core.money(s['rev_month'])} so'm\n"
            f"🤖 Userbotlar: {on}/{len(pool)} ulangan · 💳 To'lov akkaunti: {'✅' if pc else '❌'}\n"
            + (f"\n🧾 Tekshiruvni kutayotgan cheklar: {rev}" if rev else "")
            + (f"\n📍 GPS so'rovlari: {gpsq}" if gpsq else ""))
    rows = [
        [B(text="📊 Statistika", callback_data="ad:stats"), B(text="👥 Foydalanuvchilar", callback_data="ad:users:all:0")],
        [B(text=f"💳 To'lovlar{f' ({rev})' if rev else ''}", callback_data="ad:pay"),
         B(text="⚙️ Avto to'lov", callback_data="ad:apay")],
        [B(text=f"🤖 Userbotlar ({on}/{len(pool)})", callback_data="ad:pool"),
         B(text=f"📍 GPS so'rovlar{f' ({gpsq})' if gpsq else ''}", callback_data="ad:gps")],
        [B(text="⭐ VIP", callback_data="ad:vip"), B(text="👮 Adminlar", callback_data="ad:admins")],
        [B(text="📣 Xabar yuborish", callback_data="ad:bc"), B(text="📥 Excel", callback_data="ad:xls")],
        [B(text="🔎 Qidirish", callback_data="ad:search"), B(text="🔄 Yangilash", callback_data="ad:home")],
    ]
    return text, K(inline_keyboard=rows)


async def show_home(m: Message, uid: int, edit=False):
    text, kb = home_view(uid)
    if edit:
        await core.safe_edit(m, text, kb)
    else:
        await m.answer(text, reply_markup=kb)


def stats_text():
    s = dbm.global_stats()
    return (f"📊 STATISTIKA\n\n"
            f"👥 Jami: {s['users']}\n  👔 Menejer: {s['managers']}\n  🚛 Haydovchi: {s['drivers']}\n"
            f"  ❔ Ro'yxatdan o'tmagan: {s['noreg']}\n  🚫 Bloklangan: {s['blocked']}\n"
            f"🆕 Bugun qo'shilgan: {s['new_today']} · 🟢 Bugun faol: {s['active_today']}\n\n"
            f"✅ To'lagan (faol): {s['paid']}\n🆓 Sinovda: {s['trial']}\n⛔ Muddati tugagan: {s['expired']}\n⭐ VIP: {s['vip']}\n\n"
            f"💰 Daromad:\n  Bugun: {core.money(s['rev_today'])} so'm ({s['pay_today']} ta)\n"
            f"  Bu oy: {core.money(s['rev_month'])} so'm ({s['pay_month']} ta)\n"
            f"  Jami: {core.money(s['rev_all'])} so'm ({s['pay_all']} ta)\n\n"
            f"🚛 Furalar: {s['vehicles']} · 👤 Haydovchilar (baza): {s['drivers_db']} · 📄 Hujjatlar: {s['docs']}\n"
            f"📍 GPS ulanganlar: {s['gps_users']}\n🔎 Qidiruvlar: {s['searches']} · 💬 Yozilgan e'lon egalari: {s['leads']}\n"
            f"📢 Faol avto xabarlar: {s['campaigns']}\n\n"
            f"🤖 AI chaqiruvlari (bot ishga tushgandan beri): Groq {llm.STATS['groq']} · Gemini {llm.STATS['gemini']} · "
            f"Claude {llm.STATS['claude']} · xato {llm.STATS['errors']}\n"
            f"Arzon AI: {'✅ ' + ', '.join(n for n, on in (('Groq', config.GROQ_API_KEYS), ('Gemini', config.CHEAP_AI_KEY)) if on) if llm.cheap_enabled() else '❌ ulanmagan (hammasi Claude da)'}")


# ====================================================================== FOYDALANUVCHILAR

def users_view(flt, page):
    total = dbm.count_users(flt)
    rows_u = dbm.list_users(flt, page * PAGE, PAGE)
    rows = []
    for u in rows_u:
        st, _ = dbm.sub_state(u)
        icon = STATE_UZ.get(st, st).split(" ")[0]
        role = {"manager": "M", "driver": "H"}.get(u.get("role"), "?")
        rows.append([B(text=f"{icon} {role} · {(u.get('name') or '-')[:22]} · {u['user_id']}"[:60],
                       callback_data=f"ad:u:{u['user_id']}")])
    nav = []
    pages = max(1, (total - 1) // PAGE + 1)
    if page > 0:
        nav.append(B(text="⬅️", callback_data=f"ad:users:{flt}:{page - 1}"))
    nav.append(B(text=f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(B(text="➡️", callback_data=f"ad:users:{flt}:{page + 1}"))
    rows.append(nav)
    fl = [B(text=("• " if f == flt else "") + t, callback_data=f"ad:users:{f}:0") for f, t in FILTERS]
    rows += [fl[i:i + 3] for i in range(0, len(fl), 3)]
    rows.append([B(text="🔎 Qidirish", callback_data="ad:search")])
    rows.append(back())
    title = dict(FILTERS).get(flt, flt)
    return f"👥 Foydalanuvchilar - {title}: {total} ta\n(M - menejer, H - haydovchi)", K(inline_keyboard=rows)


def user_card(uid):
    u = dbm.get_user(uid)
    if not u:
        return "Foydalanuvchi topilmadi.", K(inline_keyboard=[back("ad:users:all:0")])
    st, until = dbm.sub_state(u)
    s = dbm.user_stats(uid)
    role = {"manager": "👔 Menejer", "driver": "🚛 Haydovchi"}.get(u.get("role"), "❔ tanlanmagan")
    if u.get("role") == "driver" and u.get("manager_id"):
        mu = dbm.get_user(u["manager_id"]) or {}
        role += f" ({mu.get('company') or mu.get('name') or u['manager_id']})"
    camp = dbm.ap_get(uid)
    lines = [
        f"👤 {_uname(u)}", f"🆔 ID: {uid}", f"📞 {u.get('phone') or '-'}", f"Rol: {role}",
        f"Holat: {STATE_UZ.get(st, st)}" + (f" - {dbm.fmt_dt(until, True)} gacha" if until else ""),
    ]
    if u.get("company"):
        lines.append(f"🏢 Kompaniya: {u['company']}")
    lines += [
        f"📍 GPS: {'✅ ulangan' if u.get('gps_token') else '-'}",
        f"📢 Avto xabar: {'▶️ ishlamoqda' if camp and camp['active'] else '-'}",
        f"🚛 Furalar: {s['vehicles']} · 👤 Haydovchilar: {s['drivers']} (botda {s['linked_drivers']}) · 📄 Hujjatlar: {s['docs']}",
        f"💰 To'lovlar jami: {core.money(s['payments'])} so'm · 🔎 Qidiruvlar: {s['searches']}",
        f"📅 Ro'yxatdan: {dbm.fmt_dt(u.get('created'))} · 🕒 Oxirgi faollik: {dbm.fmt_dt(u.get('last_seen'), True)}",
    ]
    rows = [
        [B(text="✅ Blokdan chiqarish", callback_data=f"ad:unblock:{uid}") if u.get("blocked")
         else B(text="🚫 Bloklash", callback_data=f"ad:block:{uid}"),
         B(text="⭐ VIPni olish", callback_data=f"ad:vipoff:{uid}") if u.get("vip")
         else B(text="⭐ VIP berish", callback_data=f"ad:vipon:{uid}")],
        [B(text="➕ 7 kun", callback_data=f"ad:ext:{uid}:7"), B(text="➕ 30 kun", callback_data=f"ad:ext:{uid}:30"),
         B(text="➕ N kun", callback_data=f"ad:extn:{uid}")],
        [B(text="⛔ Obunani tugatish", callback_data=f"ad:end:{uid}"), B(text="✉️ Xabar yozish", callback_data=f"ad:msg:{uid}")],
        [B(text="📍 GPS token kiritish", callback_data=f"ad:gtok:{uid}")]
        + ([B(text="🗑 GPS ni o'chirish", callback_data=f"ad:gdel:{uid}")] if u.get("gps_token") else []),
        back("ad:users:all:0", "🔙 Ro'yxat"),
    ]
    return "\n".join(lines), K(inline_keyboard=rows)


# ====================================================================== TO'LOVLAR

def pay_view():
    rev = dbm.payments_by_status("review")
    lines = ["💳 TO'LOVLAR\n"]
    for p in dbm.list_payments(limit=10):
        u = dbm.get_user(p["user_id"]) or {}
        lines.append(f"✅ {dbm.fmt_dt(p['paid_at'], True)} · {core.money(p['amount'])} so'm · "
                     f"{(u.get('name') or '-')[:18]} · {p['method']}")
    if len(lines) == 1:
        lines.append("Hali to'lov yo'q.")
    rows = [[B(text=f"🧾 Tekshiruvdagi cheklar ({len(rev)})", callback_data="ad:payrev")],
            [B(text="💵 Kartaga oxirgi tushumlar", callback_data="ad:txns")],
            back()]
    return "\n".join(lines), K(inline_keyboard=rows)


def txns_view():
    lines = ["💵 KARTAGA OXIRGI XABARLAR (@CardXabarBot)\n"]
    for t in dbm.recent_txns(15):
        sign = "➕" if t["is_credit"] else "➖"
        tag = f" → to'lov #{t['payment_id']}" if t["payment_id"] else (" (bog'lanmagan)" if t["is_credit"] else "")
        lines.append(f"{sign} {core.money(t['amount'])} · *{t['card'] or '?'} · {dbm.fmt_dt(t['txn_time'] or t['received_at'], True)}{tag}")
    if len(lines) == 1:
        lines.append("Hali tushum yo'q. To'lov akkaunti ulanganmi va @CardXabarBot xabar yuboryaptimi?")
    return "\n".join(lines), K(inline_keyboard=[[B(text="🔄 Hozir tekshirish", callback_data="ad:txsync")],
                                               back("ad:pay")])


def apay_view():
    s = dbm.settings()
    pa, pc = userbot.pay_client()
    card = dbm.digits(s["card_number"])
    lines = [
        "⚙️ AVTO TO'LOV SOZLAMALARI\n",
        f"💳 To'lov akkaunti: {('✅ ' + (pa['name'] or pa['phone'])) if pc else ('❌ ulanmagan' if not pa else '⚠️ ' + (pa['name'] or '') + ' (uzilgan)')}",
        f"🔁 Avto tekshiruv: {'✅ yoqilgan' if s['auto_pay'] == '1' else '⏸ o' + chr(39) + 'chirilgan (cheklar adminga boradi)'}",
        f"💳 Karta: {' '.join(card[i:i + 4] for i in range(0, len(card), 4)) or '-'}",
        f"👤 Karta egasi: {s['card_holder'] or '-'}",
        f"💰 Narx: {core.money(s['price'])} so'm / {s['period_days']} kun",
        f"🆓 Yangi menejerga sinov: {s['trial_days']} kun",
        f"⏳ To'lov oynasi: {s['pay_window_min']} daqiqa",
        f"💬 Foydalanuvchi kunlik DM limiti: {s['user_dm_day']}",
        f"🆘 Yordam: {s['support'] or '-'}",
        "",
        "Qanday ishlaydi: to'lov akkauntiga @CardXabarBot tushum haqida yozadi, bot har bir to'lovchiga unikal "
        "summa beradi (masalan 100 347) va tushum kelishi bilan obunani avtomatik ochadi.",
    ]
    rows = [
        [B(text="🔗 To'lov akkauntini ulash" if not pa else "🔁 Akkauntni almashtirish", callback_data="ad:payacc")]
        + ([B(text="🔌 Uzish", callback_data="ad:payaccoff")] if pa else []),
        [B(text="⏸ Avto tekshiruvni o'chirish" if s["auto_pay"] == "1" else "▶️ Avto tekshiruvni yoqish",
           callback_data="ad:autotoggle")],
    ]
    keys = list(SETTING_TITLES)
    btns = [B(text=SETTING_TITLES[k][0], callback_data=f"ad:set:{k}") for k in keys]
    rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([B(text="🧪 CardXabar ni tekshirish", callback_data="ad:paytest")])
    rows.append(back())
    return "\n".join(lines), K(inline_keyboard=rows)


async def send_review(admin_id, p):
    u = dbm.get_user(p["user_id"]) or {}
    info = json.loads(p.get("receipt_info") or "{}") or {}
    cap = (f"🧾 To'lov #{p['id']} - {_uname(u)} (ID {p['user_id']})\n"
           f"💰 Kutilgan: {core.money(p['amount'])} so'm · Chekda: "
           f"{core.money(info['amount']) if info.get('amount') else '?'} so'm\n❗ {p.get('note') or ''}")
    kb = K(inline_keyboard=[[B(text="✅ Tasdiqlash", callback_data=f"pa:{p['id']}"),
                             B(text="❌ Rad etish", callback_data=f"pr:{p['id']}")]])
    if p.get("receipt_file_id"):
        if p.get("receipt_kind") == "photo":
            await core.bot.send_photo(admin_id, p["receipt_file_id"], caption=cap[:1000], reply_markup=kb)
        else:
            await core.bot.send_document(admin_id, p["receipt_file_id"], caption=cap[:1000], reply_markup=kb)
    else:
        await core.bot.send_message(admin_id, cap, reply_markup=kb)


# ====================================================================== USERBOTLAR (POOL)

def pool_view():
    accs = dbm.acc_list("pool")
    lines = ["🤖 YUK QIDIRISH USERBOTLARI\n",
             f"Har biri kuniga {config.MAX_DMS_PER_ACCOUNT_DAY} tagacha yangi odamga yozadi. "
             "Ko'proq akkaunt = ko'proq foydalanuvchiga xizmat.\n"]
    rows = []
    for a in accs:
        on = userbot.is_connected(a["id"])
        icon = "🟢" if on else ("⏸" if not a["active"] else "🔴")
        g = len(dbm.pool_groups(a["id"]))
        dm = dbm.dms_today_account(a["id"])
        lines.append(f"{icon} #{a['id']} {a['name'] or a['phone']} · guruh: {g} · bugun: {dm}/{config.MAX_DMS_PER_ACCOUNT_DAY}")
        rows.append([B(text=f"{icon} #{a['id']} {a['name'] or a['phone']}"[:60], callback_data=f"ad:acc:{a['id']}")])
    if not accs:
        lines.append("Hali userbot yo'q. «➕ Akkaunt qo'shish» ni bosing.")
    groups = dbm.pool_all_groups()
    lines.append(f"\n📡 Jami kuzatilayotgan guruhlar (takrorlanmas): {len(groups)}")
    rows.append([B(text="➕ Akkaunt qo'shish", callback_data="ad:pooladd")])
    rows.append(back())
    return "\n".join(lines), K(inline_keyboard=rows)


def account_view(aid):
    a = dbm.acc_get(aid)
    if not a:
        return "Akkaunt topilmadi.", K(inline_keyboard=[back("ad:pool")])
    on = userbot.is_connected(aid)
    groups = dbm.pool_groups(aid)
    lines = [f"🤖 Userbot #{aid}", f"👤 {a['name'] or '-'}" + (f" (@{a['username']})" if a.get("username") else ""),
             f"📞 {a['phone']}", f"Holat: {'🟢 ulangan' if on else ('⏸ to' + chr(39) + 'xtatilgan' if not a['active'] else '🔴 uzilgan')}"
             + (f" ({a['status']})" if a.get("status") and a["status"] != "ok" else ""),
             f"💬 Bugun yozgan: {dbm.dms_today_account(aid)}/{config.MAX_DMS_PER_ACCOUNT_DAY}",
             f"📡 Guruhlar: {len(groups)} ta"]
    lines += [f"  • {g['title']}" for g in groups[:20]]
    if len(groups) > 20:
        lines.append(f"  ... va yana {len(groups) - 20} ta")
    rows = [[B(text="📡 Guruhlarni tanlash", callback_data=f"ad:accgr:{aid}")],
            [B(text="⏸ To'xtatish", callback_data=f"ad:accoff:{aid}") if a["active"]
             else B(text="▶️ Yoqish", callback_data=f"ad:accon:{aid}"),
             B(text="🗑 O'chirish", callback_data=f"ad:accdel:{aid}")],
            back("ad:pool")]
    return "\n".join(lines), K(inline_keyboard=rows)


async def show_account(m: Message, aid: int):
    text, kb = account_view(aid)
    await m.answer(text, reply_markup=kb)


# ====================================================================== GPS SO'ROVLARI

def gps_view():
    reqs = dbm.gps_req_pending()
    lines = ["📍 GPS ULASH SO'ROVLARI\n"]
    rows = []
    for r in reqs:
        u = dbm.get_user(r["user_id"]) or {}
        lines.append(f"#{r['id']} · {_uname(u)} · {dbm.fmt_dt(r['created'], True)}")
        rows.append([B(text=f"#{r['id']} {(u.get('name') or '-')[:30]}", callback_data=f"ad:gr:{r['id']}")])
    if not reqs:
        lines.append("Kutilayotgan so'rov yo'q.")
    rows.append(back())
    return "\n".join(lines), K(inline_keyboard=rows)


def gps_req_view(rid):
    r = dbm.gps_req_get(rid)
    if not r:
        return "So'rov topilmadi.", K(inline_keyboard=[back("ad:gps")])
    u = dbm.get_user(r["user_id"]) or {}
    try:
        pwd = core.dec(r["password"]) if r["password"] else "-"
    except Exception:
        pwd = "(ochib bo'lmadi)"
    text = (f"📍 GPS so'rovi #{rid} ({r['status']})\n👤 {_uname(u)} (ID {r['user_id']})\n📞 {u.get('phone') or '-'}\n\n"
            f"🌐 Server: {r['server']}\n👤 Login: {r['login']}\n🔑 Parol: {pwd}\n\n"
            "TOKEN OLISH (1 daqiqa):\n"
            "1) Pastdagi «🌐 Token sahifasi» tugmasini bosing (brauzerda ochiladi).\n"
            "2) Yuqoridagi login va parolni kiriting → «Kirish/Войти».\n"
            "3) Ochilgan sahifaning MANZIL SATRIDA «access_token=…» bo'ladi. Manzilni to'liq nusxalang.\n"
            "4) «🔑 Token kiritish» ni bosib, nusxalangan manzilni shu yerga yuboring - bot tokenni o'zi ajratib oladi.\n\n"
            "Login/parol xato bo'lsa - «❌ Rad etish». Token saqlangach parol bazadan o'chiriladi.")
    rows = [[B(text="🌐 Token sahifasi", url=gps.oauth_link(r["server"]))],
            [B(text="🔁 Avto urinish", callback_data=f"ad:ga:{rid}")],
            [B(text="🔑 Token kiritish", callback_data=f"ad:gt:{rid}"), B(text="❌ Rad etish", callback_data=f"ad:gx:{rid}")],
            back("ad:gps")]
    return text, K(inline_keyboard=rows)


async def notify_gps_request(rid):
    text, kb = gps_req_view(rid)
    await core.notify_admins("🆕 " + text, kb)


async def save_gps_token(admin_id, uid, server, token, rid=None) -> str:
    ok, info = await gps.check_token(server, token)
    if not ok:
        return f"❌ Token ishlamadi: {info}\nTekshirib qayta yuboring."
    dbm.set_user(uid, gps_url=gps.site_root(server), gps_token=core.enc(token))
    if rid:
        dbm.gps_req_close(rid, "done", admin_id)
    await core.send(uid, "✅ GPS muvaffaqiyatli o'rnatildi!\n\nEndi «📍 GPS» tugmasi orqali furalaringiz joylashuvi, "
                         "tezligi va yurgan km ini ko'rishingiz mumkin. Botga «01140WLA qayerda?» deb yozsangiz ham bo'ladi.")
    return f"✅ Token saqlandi ({info}). Foydalanuvchiga xabar yuborildi."


# ====================================================================== VIP / ADMINLAR

def vip_view():
    users = dbm.list_users("vip", 0, 50)
    lines = ["⭐ VIP FOYDALANUVCHILAR (botdan bepul foydalanadi)\n"]
    rows = []
    for u in users:
        lines.append(f"⭐ {_uname(u)} · {u['user_id']}")
        rows.append([B(text=f"❌ {(u.get('name') or str(u['user_id']))[:30]}", callback_data=f"ad:vipdel:{u['user_id']}")])
    if not users:
        lines.append("Hali VIP yo'q.")
    rows.append([B(text="➕ VIP qo'shish (ID orqali)", callback_data="ad:vipadd")])
    rows.append(back())
    return "\n".join(lines), K(inline_keyboard=rows)


def admins_view(uid):
    lines = ["👮 ADMINLAR\n"]
    for sid in config.SUPER_ADMINS:
        u = dbm.get_user(sid) or {"name": "-"}
        lines.append(f"👑 {_uname(u)} · {sid} (bosh admin)")
    rows = []
    for a in dbm.list_admins():
        u = dbm.get_user(a["user_id"]) or {"name": "-"}
        lines.append(f"👮 {_uname(u)} · {a['user_id']}")
        if config.is_super(uid):
            rows.append([B(text=f"❌ {(u.get('name') or str(a['user_id']))[:30]}", callback_data=f"ad:admdel:{a['user_id']}")])
    if config.is_super(uid):
        rows.append([B(text="➕ Admin qo'shish (ID orqali)", callback_data="ad:admadd")])
    else:
        lines.append("\nAdmin qo'shish/o'chirish faqat bosh admin uchun.")
    rows.append(back())
    return "\n".join(lines), K(inline_keyboard=rows)


# ====================================================================== EXCEL

def users_excel() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Foydalanuvchilar"
    head = ["ID", "Ism", "Username", "Telefon", "Rol", "Kompaniya", "Holat", "Gacha", "VIP", "Blok",
            "Furalar", "Haydovchilar", "Hujjatlar", "To'lovlar (so'm)", "Ro'yxatdan", "Oxirgi faollik"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="263238")
    for u in dbm.list_users("all", 0, 100000):
        st, until = dbm.sub_state(u)
        s = dbm.user_stats(u["user_id"])
        ws.append([u["user_id"], u.get("name"), u.get("username"), u.get("phone"), u.get("role"), u.get("company"),
                   st, dbm.fmt_dt(until, True) if until else "", "ha" if u.get("vip") else "",
                   "ha" if u.get("blocked") else "", s["vehicles"], s["drivers"], s["docs"], s["payments"],
                   dbm.fmt_dt(u.get("created"), True), dbm.fmt_dt(u.get("last_seen"), True)])
    for col, w in zip("ABCDEFGHIJKLMNOP", (14, 24, 16, 15, 10, 20, 12, 17, 5, 5, 8, 11, 10, 14, 17, 17)):
        ws.column_dimensions[col].width = w
    ws2 = wb.create_sheet("To'lovlar")
    ws2.append(["#", "User ID", "Ism", "Summa", "Kun", "Usul", "Sana"])
    for p in dbm.list_payments(limit=100000):
        u = dbm.get_user(p["user_id"]) or {}
        ws2.append([p["id"], p["user_id"], u.get("name"), p["amount"], p["days"], p["method"], dbm.fmt_dt(p["paid_at"], True)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ====================================================================== OMMAVIY XABAR

BC_SEGMENTS = [("all", "Hammaga"), ("managers", "Menejerlarga"), ("drivers", "Haydovchilarga"),
               ("trial", "Sinovdagilarga"), ("expired", "Muddati tugaganlarga"), ("paid", "To'laganlarga")]


async def _broadcast(admin_id, seg, chat_id, msg_id):
    ids = dbm.user_ids_by_filter(seg)
    ok = bad = 0
    for i, uid in enumerate(ids):
        try:
            await core.bot.copy_message(uid, chat_id, msg_id)
            ok += 1
        except Exception:
            bad += 1
        await asyncio.sleep(0.05)
        if i and i % 500 == 0:
            await core.send(admin_id, f"📣 Yuborilmoqda... {i}/{len(ids)}")
    await core.send(admin_id, f"📣 Xabar yuborildi: ✅ {ok} · ❌ {bad} (botni bloklaganlar)")


async def bc_preview(m: Message, w: dict):
    w.update(chat_id=m.chat.id, msg_id=m.message_id, k="admin:bcready")
    n = len(dbm.user_ids_by_filter(w["seg"]))
    await m.answer(f"👆 Shu xabar {n} ta foydalanuvchiga yuboriladi. Tasdiqlaysizmi?",
                   reply_markup=K(inline_keyboard=[[B(text="✅ Yuborish", callback_data="ad:bcgo"),
                                                    B(text="❌ Bekor", callback_data="ad:bcno")]]))


# ====================================================================== MATN KIRITISH

async def handle_input(m: Message, w: dict):
    uid = m.from_user.id
    k = w["k"]
    text = (m.text or "").strip()
    if not dbm.is_admin(uid):
        core.WAIT.pop(uid, None)
        return
    if k == "admin:search":
        core.WAIT.pop(uid, None)
        found = dbm.find_users(text)
        if not found:
            await m.answer("🤷 Topilmadi. Qayta: 🔎 Qidirish", reply_markup=K(inline_keyboard=[[B(text="🔎 Qidirish", callback_data="ad:search")], back()]))
            return
        rows = [[B(text=f"{STATE_UZ.get(dbm.sub_state(u)[0], '').split(' ')[0]} {(u.get('name') or '-')[:30]} · {u['user_id']}",
                   callback_data=f"ad:u:{u['user_id']}")] for u in found]
        rows.append(back())
        await m.answer(f"🔎 Topildi: {len(found)}", reply_markup=K(inline_keyboard=rows))
    elif k == "admin:ext":
        if not text.isdigit() or not (1 <= int(text) <= 3650):
            await m.answer("Kun sonini raqamda yuboring (1-3650).")
            return
        core.WAIT.pop(uid, None)
        until = dbm.extend_sub(w["uid"], int(text))
        await core.send(w["uid"], f"🎁 Obunangiz {dbm.fmt_dt(until)} gacha uzaytirildi.")
        t, kb = user_card(w["uid"])
        await m.answer(f"✅ {text} kun qo'shildi.\n\n" + t, reply_markup=kb)
    elif k == "admin:msg":
        core.WAIT.pop(uid, None)
        ok = await core.send(w["uid"], f"✉️ Admindan xabar:\n\n{text}")
        await m.answer("✅ Yuborildi." if ok else "❌ Yuborib bo'lmadi (botni bloklagan bo'lishi mumkin).")
    elif k == "admin:gtok":
        token = gps.extract_token(text)
        if len(token) < 20:
            await m.answer("Token juda qisqa. Wialon tokenini to'liq yuboring.")
            return
        server = w.get("server") or config.GPS_DEFAULT_SERVER
        if len(text.split()) == 2 and "access_token" not in text:
            server = text.split()[0]
        wait = await m.answer("⏳ Token tekshirilmoqda...")
        res = await save_gps_token(uid, w["uid"], server, token, w.get("rid"))
        if res.startswith("✅"):
            core.WAIT.pop(uid, None)
            try:
                await m.delete()
            except Exception:
                pass
        await core.safe_edit(wait, res)
    elif k == "admin:set":
        key = w["key"]
        val = text
        if key in NUMERIC:
            if not val.replace(" ", "").isdigit():
                await m.answer("Raqam yuboring.")
                return
            val = str(int(val.replace(" ", "")))
            if int(val) <= 0:
                await m.answer("0 dan katta bo'lsin.")
                return
        elif key == "card_number":
            val = dbm.digits(val)
            if len(val) != 16:
                await m.answer("Karta raqami 16 ta raqam bo'lishi kerak.")
                return
        elif key == "support":
            val = "@" + val.lstrip("@") if val else ""
        core.WAIT.pop(uid, None)
        dbm.set_setting(key, val)
        t, kb = apay_view()
        await m.answer("✅ Saqlandi.\n\n" + t, reply_markup=kb)
    elif k == "admin:vipadd":
        if not text.lstrip("-").isdigit():
            await m.answer("Telegram ID ni raqamda yuboring (foydalanuvchi /id buyrug'i bilan bilib oladi).")
            return
        core.WAIT.pop(uid, None)
        tid = int(text)
        dbm.set_user(tid, vip=1)
        await core.send(tid, "⭐ Sizga VIP maqom berildi - bot siz uchun to'liq bepul! /start")
        t, kb = vip_view()
        await m.answer(f"✅ {tid} VIP qilindi.\n\n" + t, reply_markup=kb)
    elif k == "admin:admadd":
        if not config.is_super(uid):
            core.WAIT.pop(uid, None)
            return
        if not text.lstrip("-").isdigit():
            await m.answer("Telegram ID ni raqamda yuboring.")
            return
        core.WAIT.pop(uid, None)
        tid = int(text)
        dbm.ensure_user(tid)
        dbm.add_admin(tid, uid)
        await core.send(tid, "👮 Siz botga admin qilib tayinlandingiz. /admin")
        t, kb = admins_view(uid)
        await m.answer(f"✅ {tid} admin qilindi.\n\n" + t, reply_markup=kb)


# ====================================================================== TUGMALAR

@router.callback_query(F.data.startswith("ad:"))
async def cb_admin(cb: CallbackQuery):
    uid = cb.from_user.id
    if not dbm.is_admin(uid):
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    p = cb.data.split(":")
    act = p[1]
    msg = cb.message
    alert = None

    async def view(pair):
        await core.safe_edit(msg, pair[0], pair[1])

    if act == "home":
        await view(home_view(uid))
    elif act == "stats":
        await core.safe_edit(msg, stats_text(), K(inline_keyboard=[back()]))
    elif act == "users":
        await view(users_view(p[2], int(p[3])))
    elif act == "u":
        await view(user_card(int(p[2])))
    elif act == "search":
        core.WAIT[uid] = {"k": "admin:search"}
        await msg.answer("🔎 ID, @username, telefon yoki ism yuboring:")
    elif act in ("block", "unblock", "vipon", "vipoff", "end"):
        tid = int(p[2])
        if act == "block":
            if dbm.is_admin(tid):
                alert = "Adminni bloklab bo'lmaydi"
            else:
                dbm.set_user(tid, blocked=1)
                camp = dbm.ap_get(tid)
                if camp:
                    dbm.ap_update(tid, active=0)
        elif act == "unblock":
            dbm.set_user(tid, blocked=0)
        elif act == "vipon":
            dbm.set_user(tid, vip=1)
            await core.send(tid, "⭐ Sizga VIP maqom berildi - bot siz uchun to'liq bepul!")
        elif act == "vipoff":
            dbm.set_user(tid, vip=0)
        elif act == "end":
            dbm.end_sub(tid)
        await view(user_card(tid))
    elif act == "ext":
        tid, days = int(p[2]), int(p[3])
        until = dbm.extend_sub(tid, days)
        await core.send(tid, f"🎁 Obunangiz {dbm.fmt_dt(until)} gacha uzaytirildi.")
        alert = f"+{days} kun"
        await view(user_card(tid))
    elif act == "extn":
        core.WAIT[uid] = {"k": "admin:ext", "uid": int(p[2])}
        await msg.answer("Necha kun qo'shilsin? Raqam yuboring:")
    elif act == "msg":
        core.WAIT[uid] = {"k": "admin:msg", "uid": int(p[2])}
        await msg.answer("✉️ Foydalanuvchiga yuboriladigan xabarni yozing:")
    elif act == "gtok":
        tid = int(p[2])
        u = dbm.get_user(tid) or {}
        core.WAIT[uid] = {"k": "admin:gtok", "uid": tid, "server": u.get("gps_url") or config.GPS_DEFAULT_SERVER}
        await msg.answer(f"🔑 Wialon tokenini yuboring.\nServer: {u.get('gps_url') or config.GPS_DEFAULT_SERVER}\n"
                         "(boshqa server bo'lsa: «server token» ko'rinishida yuboring)")
    elif act == "gdel":
        tid = int(p[2])
        dbm.set_user(tid, gps_token=None)
        await view(user_card(tid))

    # ---- to'lovlar
    elif act == "pay":
        await view(pay_view())
    elif act == "payrev":
        rev = dbm.payments_by_status("review")
        if not rev:
            alert = "Tekshiruvdagi chek yo'q"
        for pp in rev[:10]:
            try:
                await send_review(uid, pp)
            except Exception:
                logging.exception("Chekni ko'rsatib bo'lmadi")
    elif act == "txns":
        await view(txns_view())
    elif act == "txsync":
        await payments.catch_up()
        await view(txns_view())
        alert = "Tekshirildi"
    elif act == "apay":
        await view(apay_view())
    elif act == "set":
        key = p[2]
        core.WAIT[uid] = {"k": "admin:set", "key": key}
        await msg.answer(f"{SETTING_TITLES[key][0]}\n{SETTING_TITLES[key][1]}:\n(bekor qilish: /cancel)")
    elif act == "autotoggle":
        dbm.set_setting("auto_pay", "0" if dbm.setting("auto_pay") == "1" else "1")
        await view(apay_view())
    elif act == "payacc":
        if not config.TG_API_ID or not config.TG_API_HASH:
            alert = "TG_API_ID / TG_API_HASH sozlanmagan"
        else:
            await login.begin(msg, uid, "pay")
    elif act == "payaccoff":
        a = dbm.pay_account()
        if a:
            await userbot.remove(a["id"], logout=False)
        await view(apay_view())
    elif act == "paytest":
        ok = await payments.check_card_bot()
        await payments.catch_up()
        alert = ("✅ @CardXabarBot chati topildi, oxirgi xabarlar o'qildi." if ok else
                 "❌ To'lov akkauntida @CardXabarBot chati topilmadi yoki akkaunt ulanmagan.")

    # ---- userbotlar
    elif act == "pool":
        await view(pool_view())
    elif act == "pooladd":
        if not config.TG_API_ID or not config.TG_API_HASH:
            alert = "TG_API_ID / TG_API_HASH sozlanmagan"
        else:
            await login.begin(msg, uid, "pool")
    elif act == "acc":
        await view(account_view(int(p[2])))
    elif act == "accgr":
        await pickers.show(msg, uid, "p", int(p[2]), 0, refresh=True)
    elif act == "accoff":
        await userbot.pause(int(p[2]))
        await view(account_view(int(p[2])))
    elif act == "accon":
        ok = await userbot.resume(int(p[2]))
        alert = None if ok else "Ulab bo'lmadi - sessiya tugagan bo'lishi mumkin. O'chirib, qayta qo'shing."
        await view(account_view(int(p[2])))
    elif act == "accdel":
        await core.safe_edit(msg, f"🗑 Userbot #{p[2]} o'chirilsinmi? (Telegram'dagi sessiyasi ham yopiladi)",
                             K(inline_keyboard=[[B(text="✅ Ha, o'chir", callback_data=f"ad:accdel2:{p[2]}"),
                                                 B(text="❌ Yo'q", callback_data=f"ad:acc:{p[2]}")]]))
    elif act == "accdel2":
        await userbot.remove(int(p[2]), logout=True)
        await view(pool_view())

    # ---- GPS
    elif act == "gps":
        await view(gps_view())
    elif act == "gr":
        await view(gps_req_view(int(p[2])))
    elif act == "gt":
        r = dbm.gps_req_get(int(p[2]))
        if not r or r["status"] != "pending":
            alert = "So'rov allaqachon ko'rib chiqilgan"
        else:
            core.WAIT[uid] = {"k": "admin:gtok", "uid": r["user_id"], "server": r["server"], "rid": r["id"]}
            await msg.answer(f"🔑 #{r['id']} uchun Wialon tokenini yuboring (server: {r['server']}):")
    elif act == "ga":
        r = dbm.gps_req_get(int(p[2]))
        if not r or r["status"] != "pending":
            alert = "So'rov allaqachon ko'rib chiqilgan"
        else:
            cred, info = await gps.auto_connect(r["server"], r["login"], core.dec(r["password"]))
            if cred:
                res = await save_gps_token(uid, r["user_id"], r["server"], cred, r["id"])
                await msg.answer(res)
            else:
                alert = f"Avtomatik bo'lmadi: {info}"[:180]
    elif act == "gx":
        r = dbm.gps_req_get(int(p[2]))
        if r and r["status"] == "pending":
            dbm.gps_req_close(r["id"], "rejected", uid)
            await core.send(r["user_id"], "❌ GPS ulash so'rovingiz bajarilmadi. Login/parolni tekshirib, qayta yuboring "
                                          "yoki admin bilan bog'laning.")
        await view(gps_view())

    # ---- VIP / adminlar
    elif act == "vip":
        await view(vip_view())
    elif act == "vipadd":
        core.WAIT[uid] = {"k": "admin:vipadd"}
        await msg.answer("⭐ VIP qilinadigan foydalanuvchining Telegram ID sini yuboring (u /id orqali biladi):")
    elif act == "vipdel":
        dbm.set_user(int(p[2]), vip=0)
        await view(vip_view())
    elif act == "admins":
        await view(admins_view(uid))
    elif act == "admadd":
        if not config.is_super(uid):
            alert = "Faqat bosh admin"
        else:
            core.WAIT[uid] = {"k": "admin:admadd"}
            await msg.answer("👮 Yangi adminning Telegram ID sini yuboring:")
    elif act == "admdel":
        if config.is_super(uid):
            dbm.remove_admin(int(p[2]))
        await view(admins_view(uid))

    # ---- ommaviy xabar
    elif act == "bc":
        rows = [[B(text=f"{t} ({len(dbm.user_ids_by_filter(s))})", callback_data=f"ad:bcs:{s}")] for s, t in BC_SEGMENTS]
        rows.append(back())
        await core.safe_edit(msg, "📣 Kimga yuboramiz?", K(inline_keyboard=rows))
    elif act == "bcs":
        core.WAIT[uid] = {"k": "admin:bc", "seg": p[2]}
        await msg.answer("📣 Yuboriladigan xabarni yuboring (matn, rasm, video - istalgan). Bekor qilish: /cancel")
    elif act == "bcgo":
        w = core.WAIT.pop(uid, None)
        if not w or w.get("k") != "admin:bcready":
            alert = "Xabar topilmadi"
        else:
            asyncio.create_task(_broadcast(uid, w["seg"], w["chat_id"], w["msg_id"]))
            await core.safe_edit(msg, "📣 Yuborish boshlandi...")
    elif act == "bcno":
        core.WAIT.pop(uid, None)
        await core.safe_edit(msg, "❌ Bekor qilindi.")
    elif act == "xls":
        data = await asyncio.to_thread(users_excel)
        await msg.answer_document(BufferedInputFile(data, "foydalanuvchilar.xlsx"), caption="📥 Foydalanuvchilar va to'lovlar")
    try:
        await cb.answer(alert or None, show_alert=bool(alert and len(alert) > 40))
    except Exception:
        pass


@router.callback_query(F.data.startswith("pa:") | F.data.startswith("pr:"))
async def cb_payment_review(cb: CallbackQuery):
    uid = cb.from_user.id
    if not dbm.is_admin(uid):
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    pid = int(cb.data[3:])
    if cb.data.startswith("pa:"):
        ok = await payments.activate(pid, None, "manual", uid)
        note = "✅ Tasdiqlandi" if ok else "Allaqachon ko'rib chiqilgan"
    else:
        ok = await payments.reject(pid, uid)
        note = "❌ Rad etildi" if ok else "Allaqachon ko'rib chiqilgan"
    try:
        if cb.message.caption is not None:
            await cb.message.edit_caption(caption=(cb.message.caption or "") + f"\n\n{note} ({cb.from_user.full_name})")
        else:
            await core.safe_edit(cb.message, (cb.message.text or "") + f"\n\n{note} ({cb.from_user.full_name})")
    except Exception:
        pass
    await cb.answer(note)
