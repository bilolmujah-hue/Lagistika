"""Logistika Telegram boti (tijoriy versiya).
Ishga tushirish:  python bot.py

Rollar: 👔 menejer (to'liq imkoniyat, 10 kun bepul, keyin obuna), 🚛 haydovchi (menejer qo'shgan raqam bilan
kiradi, faqat o'z hujjatlari va furasini ko'radi), 🛠 admin (admin panel).
"""
import asyncio
import io
import logging
import uuid

from aiogram import BaseMiddleware, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup,
                           ReplyKeyboardRemove)

import admin
import ai
import autopost
import cargo
import config
import core
import database as dbm
import gps
import login
import payments
import pickers
import reminders
import tables
import userbot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
bot = core.bot
dp = Dispatcher()
B = InlineKeyboardButton
K = InlineKeyboardMarkup

# ---- menejer tugmalari
BTN_VEHICLES = "🚛 Furalarim"
BTN_DRIVERS = "👤 Haydovchilarim"
BTN_TABLE = "📊 Umumiy jadval"
BTN_EXPIRING = "⚠️ Muddati yaqinlar"
BTN_REMINDERS = "⏰ Eslatmalarim"
BTN_EXCEL = "📥 Excel hisobot"
BTN_GPS = "📍 GPS"
BTN_CARGO = "🔎 Yuklarni ko'rish"
BTN_AP = "📢 Avto xabar"
BTN_SUB = "💳 Obuna"
BTN_PROFILE = "👤 Profil"
BTN_HELP = "ℹ️ Yordam"
BTN_ADMIN = "🛠 Admin panel"
# ---- haydovchi tugmalari
BTN_MYDOCS = "📋 Hujjatlarim"
BTN_MYTRUCK = "🚛 Furam"
BTN_TRUCKLOC = "📍 Furam qayerda"
BTN_DREM = "⏰ Eslatmalar"

ALL_BUTTONS = {BTN_VEHICLES, BTN_DRIVERS, BTN_TABLE, BTN_EXPIRING, BTN_REMINDERS, BTN_EXCEL, BTN_GPS, BTN_CARGO,
               BTN_AP, BTN_SUB, BTN_PROFILE, BTN_HELP, BTN_ADMIN, BTN_MYDOCS, BTN_MYTRUCK, BTN_TRUCKLOC, BTN_DREM}


def kb(rows):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True)


def menu_kb(u):
    if u and u.get("role") == "driver":
        rows = [[BTN_MYDOCS, BTN_MYTRUCK], [BTN_TRUCKLOC, BTN_DREM], [BTN_PROFILE, BTN_HELP]]
    else:
        rows = [[BTN_VEHICLES, BTN_DRIVERS], [BTN_TABLE, BTN_EXPIRING], [BTN_REMINDERS, BTN_EXCEL],
                [BTN_GPS, BTN_CARGO], [BTN_AP, BTN_SUB], [BTN_PROFILE, BTN_HELP]]
    if u and dbm.is_admin(u["user_id"]):
        rows.append([BTN_ADMIN])
    return kb(rows)


HELP_MANAGER = """ℹ️ Men logistika yordamchisiman. Menga oddiy yozing - sun'iy intellekt tushunadi va bajaradi.

📄 Hujjat qo'shish: hujjat rasmini yoki PDF ni yuboring - o'qib, turini aniqlab, tasdiqlashingizni so'rayman. Fura raqami ko'rinmasa, rasmga izoh qilib yozing (masalan: 01928WNA).

💬 Masalan yozing:
• Eng kam muddati qolgan fura qaysi?
• 01928WNA ning sug'urtasi qachon tugaydi?
• Aliyev Vali +998901234567 haydovchi qo'sh va 01928WNA ga biriktir
• 01928WNA qayerda? Kecha necha km yurdi?
• Toshkentdan Moskvaga yuk kerak
• Andijondan Almatiga fura kerak
• Avto xabarni to'xtat
• Jadval ber

👤 Haydovchilar: haydovchini telefon raqami bilan qo'shing. U shu raqam bilan botga kirib «Haydovchi» ni tanlasa - o'z hujjatlari va furasini ko'radi, eslatmalar unga ham boradi.
🇨🇳 Xitoy navbati: «01928WNA Xitoydan 20.11.2026 ga navbat oldi» - Xitoyga kirish va Rossiyaga chiqish navbatini eslataman.
📍 GPS: «📍 GPS» tugmasi orqali ulang.
🔎 Yuk/fura qidirish: «🔎 Yuklarni ko'rish» - guruhlardan topaman va xohlasangiz egalari bilan o'zim gaplashaman.
📢 Avto xabar: e'loningizni tanlangan guruhlarga avtomatik yuborib turaman.

Rang belgilari: 🟢 30 kundan ko'p · 🟡 30 kungacha · 🔴 7 kun va kam yoki o'tgan · 🔵 muddatsiz · ⚪ hujjat yo'q"""

HELP_DRIVER = """ℹ️ Siz haydovchi sifatida ulangansiz.

📋 Hujjatlarim - o'z hujjatlaringiz va muddatlari
🚛 Furam - sizga biriktirilgan fura va uning hujjatlari
📍 Furam qayerda - GPS bo'yicha joylashuv
⏰ Eslatmalar - furangiz bo'yicha navbat va vazifalar

Hujjat muddati tugashidan oldin sizga ham eslatma keladi. Savolingizni oddiy yozishingiz mumkin, masalan:
«Pasportim qachon tugaydi?», «Furam kecha necha km yurdi?»"""

PENDING: dict = {}   # tasdiqlashni kutayotgan hujjatlar


# ====================================================================== MIDDLEWARE

class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)
        u = dbm.touch_user(user.id, user.full_name, user.username)
        if u.get("blocked") and not dbm.is_admin(user.id):
            if isinstance(event, Message):
                await event.answer("🚫 Siz bloklangansiz. Savol bo'lsa admin bilan bog'laning.")
            elif isinstance(event, CallbackQuery):
                await event.answer("🚫 Bloklangansiz", show_alert=True)
            return None
        if isinstance(event, Message) and event.text in ALL_BUTTONS and core.wait_kind(user.id) not in ("login",):
            core.WAIT.pop(user.id, None)
        data["u"] = u
        return await handler(event, data)


dp.message.outer_middleware(AccessMiddleware())
dp.callback_query.outer_middleware(AccessMiddleware())


# ====================================================================== RUXSAT TEKSHIRISH

def paywall_kb():
    return K(inline_keyboard=[[B(text="💳 To'lov qilish", callback_data="pay:info")]])


def paywall_text(u):
    price, days = dbm.setting_int("price", 100000), dbm.setting_int("period_days", 30)
    what = "Bepul sinov muddatingiz" if not u.get("paid_until") else "Obunangiz"
    return (f"⛔ {what} tugagan.\n\nBotning barcha imkoniyatlaridan foydalanishni davom ettirish uchun obunani "
            f"faollashtiring: {core.money(price)} so'm / {days} kun.\n\nMa'lumotlaringiz saqlanib turibdi ✅")


async def _reply(ev, text, markup=None):
    if isinstance(ev, CallbackQuery):
        await ev.answer()
        await ev.message.answer(text, reply_markup=markup)
    else:
        await ev.answer(text, reply_markup=markup)


async def need_registered(ev, u) -> bool:
    if u.get("phone") and u.get("role"):
        return True
    await _reply(ev, "Avval ro'yxatdan o'ting: /start")
    return False


async def need_manager(ev, u) -> bool:
    if not await need_registered(ev, u):
        return False
    st, _ = dbm.sub_state(u)
    if u.get("role") == "driver" and st != "admin":
        await _reply(ev, "Bu bo'lim menejerlar uchun.")
        return False
    if st in dbm.ACTIVE_STATES:
        return True
    await _reply(ev, paywall_text(u), paywall_kb())
    return False


async def need_driver(ev, u):
    """-> (menejer_id, haydovchi_yozuvi, furalar) yoki None"""
    if not await need_registered(ev, u):
        return None
    st, _ = dbm.sub_state(u)
    if st == "unlinked":
        await _reply(ev, f"⏳ Siz hali hech qaysi menejerga ulanmagansiz.\nMenejeringizga ayting: sizni {u.get('phone')} "
                         "raqami bilan haydovchi sifatida qo'shsin. Qo'shilishi bilan xabar keladi.")
        return None
    if st == "manager_expired":
        await _reply(ev, "⛔ Kompaniyangiz obunasi tugagan. Menejeringizga ayting - to'lov qilingach yana ishlaydi.")
        return None
    mid, d, vs = ai.driver_scope(u)
    if not d:
        await _reply(ev, "⏳ Siz hali menejerga ulanmagansiz.")
        return None
    return mid, d, vs


# ====================================================================== YORDAMCHI KO'RINISHLAR

async def send_long(m: Message, text: str, **kw):
    for i in range(0, len(text), 4000):
        await m.answer(text[i:i + 4000], **kw)


def doc_line(label: str, r) -> str:
    st, d = dbm.doc_status(r)
    e = dbm.STATUS_EMOJI[st]
    if st == "missing":
        return f"{e} {label} - yo'q"
    if st == "noexp":
        num = f" (№ {r['doc_number']})" if r.get("doc_number") else ""
        return f"{e} {label} - bor{num}"
    exp = dbm.fmt_date(r["expiry_date"])
    if st == "expired":
        return f"{e} {label} - {exp} (MUDDATI O'TGAN, {-d} kun)"
    return f"{e} {label} - {exp} ({d} kun qoldi)"


def worst_emoji(docs: dict) -> str:
    order = {"green": 1, "yellow": 2, "red": 3, "expired": 4}
    worst = None
    for r in docs.values():
        st, _ = dbm.doc_status(r)
        if st in order and (worst is None or order[st] > order[worst]):
            worst = st
    return dbm.STATUS_EMOJI[worst] if worst else "⚪"


def vehicle_card(uid: int, v: dict) -> str:
    docs = dbm.owner_docs(uid, "vehicle", v["id"])
    drv = dbm.get_driver(uid, v["driver_id"]) if v["driver_id"] else None
    lines = ["🚛 " + v["plate"] + (f" - {v['model']}" if v["model"] else "")]
    if v["vin"]:
        lines.append(f"VIN: {v['vin']}")
    lines.append(f"👤 Haydovchi: {drv['name'] if drv else 'biriktirilmagan'}")
    lines.append("\n📋 Hujjatlar:")
    for key, label in config.VEHICLE_DOCS.items():
        lines.append(doc_line(label, docs.get(key)))
    if v["china_queue_date"]:
        lines.append(f"\n🇨🇳 Xitoy navbati sanasi: {dbm.fmt_date(v['china_queue_date'])}")
    rems = [r for r in dbm.list_reminders(uid) if r["vehicle_id"] == v["id"]]
    if rems:
        lines.append("\n⏰ Eslatmalar:")
        for r in rems:
            lines.append(f"• {dbm.reminder_title(r)} - {dbm.fmt_date(r['remind_date'])}")
    return "\n".join(lines)


def driver_card(uid: int, d: dict, for_manager=True) -> str:
    docs = dbm.owner_docs(uid, "driver", d["id"])
    cars = dbm.vehicles_of_driver(uid, d["id"])
    lines = [f"👤 {d['name']}"]
    if d["phone"]:
        lines.append(f"📞 {d['phone']}")
    lines.append("🚛 Fura: " + (", ".join(c["plate"] for c in cars) if cars else "biriktirilmagan"))
    if for_manager:
        lines.append("🤖 Botda: " + ("✅ ulangan" if d.get("tg_user_id") else
                                     ("❌ hali kirmagan" if d.get("phone") else "❌ telefon kiritilmagan")))
    lines.append("\n📋 Hujjatlar:")
    for key, label in config.DRIVER_DOCS.items():
        lines.append(doc_line(label, docs.get(key)))
    return "\n".join(lines)


def vehicle_list_view(uid: int):
    vs = dbm.list_vehicles(uid)
    if not vs:
        return ("Hozircha fura yo'q. Fura hujjatini (texpasport, sug'urta, tahograf...) rasm yoki PDF "
                "qilib yuboring, fura o'zi qo'shiladi."), None
    by = dbm.docs_by_owner(uid, "vehicle")
    rows = []
    for v in vs:
        title = f"{worst_emoji(by.get(v['id'], {}))} {v['plate']}" + (f" · {v['model']}" if v["model"] else "")
        rows.append([B(text=title[:60], callback_data=f"v:{v['id']}")])
    return f"🚛 Furalaringiz ({len(vs)} ta). Birini tanlang:", K(inline_keyboard=rows)


def driver_list_view(uid: int):
    ds = dbm.list_drivers(uid)
    if not ds:
        return ("Hozircha haydovchi yo'q. Haydovchi hujjatini (prava, pasport, viza...) yuboring yoki yozing: "
                "«Aliyev Vali +998901234567 haydovchi qo'sh»."), None
    by = dbm.docs_by_owner(uid, "driver")
    rows = [[B(text=f"{worst_emoji(by.get(d['id'], {}))} {d['name']}{' 🤖' if d.get('tg_user_id') else ''}"[:60],
               callback_data=f"d:{d['id']}")] for d in ds]
    return (f"👤 Haydovchilaringiz ({len(ds)} ta). 🤖 - botga ulangan.\nBirini tanlang:",
            K(inline_keyboard=rows))


async def send_tables(chat_id: int, uid: int, kinds=("vehicle", "driver")):
    sent_any = False
    for kind in kinds:
        pngs = await asyncio.to_thread(tables.table_pngs, uid, kind)
        for png in pngs:
            await bot.send_photo(chat_id, BufferedInputFile(png, "jadval.png"))
            sent_any = True
    if not sent_any:
        await bot.send_message(chat_id, "Jadval uchun hali ma'lumot yo'q. Avval hujjat rasmlarini yuboring.")


async def send_stored_file(chat_id: int, file_id: str, kind: str, caption: str):
    if kind == "photo":
        await bot.send_photo(chat_id, file_id, caption=caption)
    else:
        await bot.send_document(chat_id, file_id, caption=caption)


def owner_type_of(doc_type):
    if doc_type in config.VEHICLE_DOCS:
        return "vehicle"
    if doc_type in config.DRIVER_DOCS:
        return "driver"
    return None


def sub_line(u) -> str:
    st, until = dbm.sub_state(u)
    return {"admin": "👑 Admin (cheksiz)", "vip": "⭐ VIP (bepul)",
            "paid": f"✅ To'langan - {dbm.fmt_dt(until, True)} gacha",
            "trial": f"🆓 Bepul sinov - {dbm.fmt_dt(until, True)} gacha",
            "expired": "⛔ Tugagan", "driver": "🚛 Haydovchi (kompaniya obunasi faol)",
            "manager_expired": "⛔ Kompaniya obunasi tugagan", "unlinked": "⏳ Menejerga ulanmagan"}.get(st, st)


# ====================================================================== OMMAVIY XABAR (admin) - istalgan turdagi xabar

@dp.message(lambda m: m.from_user is not None and core.wait_kind(m.from_user.id) == "admin:bc")
async def on_broadcast_content(m: Message):
    if m.text and m.text.startswith("/cancel"):
        core.WAIT.pop(m.from_user.id, None)
        await m.answer("Bekor qilindi.")
        return
    await admin.bc_preview(m, core.WAIT[m.from_user.id])


# ====================================================================== RO'YXATDAN O'TISH

WELCOME = """Assalomu alaykum! 👋 «Logistika yordamchi» botiga xush kelibsiz.

Bu bot yuk tashish kompaniyalari uchun:
📄 fura va haydovchi hujjatlarini saqlaydi, muddati tugashidan oldin eslatadi
📍 GPS orqali furalar qayerda va necha km yurganini ko'rsatadi
🔎 yuk guruhlaridan yuk yoki fura topib, egalari bilan o'zi gaplashadi
📢 e'loningizni guruhlarga avtomatik yuborib turadi
🤖 hammasini oddiy yozib boshqarasiz - sun'iy intellekt tushunadi

Ro'yxatdan o'tish uchun pastdagi «📱 Raqamni yuborish» tugmasini bosing 👇"""


def role_text():
    trial = dbm.setting_int("trial_days", 10)
    return ("Endi kim ekaningizni tanlang:\n\n"
            "👔 MENEJER - kompaniya rahbari yoki dispetcher.\n"
            "Furalar va haydovchilar hujjatlarini kiritasiz, muddati tugashidan oldin eslatma olasiz, "
            "GPS, yuk/fura qidirish, avto xabar - barcha imkoniyatlar sizda. "
            f"Yangi menejerga {trial} kun BEPUL.\n\n"
            "🚛 HAYDOVCHI - menejeringiz sizni telefon raqamingiz bilan qo'shgan bo'lsa, bot sizni avtomatik taniydi. "
            "Siz o'z hujjatlaringizni va o'zingizga biriktirilgan furaning hujjatlarini ko'rasiz, "
            "muddati tugashi va navbatlar haqida eslatma olasiz. Haydovchi uchun bepul.")


def role_kb():
    return K(inline_keyboard=[[B(text="👔 Menejer", callback_data="role:manager"),
                               B(text="🚛 Haydovchi", callback_data="role:driver")]])


@dp.message(CommandStart())
async def cmd_start(m: Message, u: dict):
    core.WAIT.pop(m.from_user.id, None)
    if not u.get("phone"):
        await m.answer(WELCOME, reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True))
        return
    if not u.get("role"):
        await m.answer(role_text(), reply_markup=role_kb())
        return
    await m.answer(f"Salom, {m.from_user.first_name}! 👋\n{sub_line(u)}\n\nPastdagi tugmalardan foydalaning yoki "
                   "menga oddiy yozing 👇", reply_markup=menu_kb(u))
    if u.get("role") == "manager" and not dbm.has_access(u):
        await m.answer(paywall_text(u), reply_markup=paywall_kb())


@dp.message(F.contact)
async def on_contact(m: Message, u: dict):
    c = m.contact
    if c.user_id and c.user_id != m.from_user.id:
        await m.answer("Iltimos, o'zingizning raqamingizni «📱 Raqamni yuborish» tugmasi orqali yuboring.")
        return
    phone = "+" + dbm.digits(c.phone_number)
    dbm.set_user(m.from_user.id, phone=phone)
    await m.answer(f"✅ Raqamingiz saqlandi: {phone}", reply_markup=ReplyKeyboardRemove())
    u = dbm.get_user(m.from_user.id)
    if u.get("role"):
        await m.answer("Asosiy menyu 👇", reply_markup=menu_kb(u))
    else:
        await m.answer(role_text(), reply_markup=role_kb())


async def _become_driver(m: Message, uid: int):
    u = dbm.get_user(uid)
    recs = [r for r in dbm.driver_records_by_phone(u.get("phone")) if not r.get("tg_user_id") or r["tg_user_id"] == uid]
    dbm.set_user(uid, role="driver")
    if recs:
        r = recs[0]
        dbm.link_driver(uid, r["user_id"], r["id"])
        mu = dbm.get_user(r["user_id"]) or {}
        company = mu.get("company") or mu.get("name") or "menejer"
        await core.send(r["user_id"], f"✅ Haydovchingiz {r['name']} ({u.get('phone')}) botga ulandi. "
                                      "Endi u o'z hujjatlari va furasini ko'radi, eslatmalar unga ham boradi.")
        u = dbm.get_user(uid)
        await m.answer(f"✅ Siz «{company}» kompaniyasining haydovchisi sifatida ulandingiz ({r['name']}).\n\n"
                       + HELP_DRIVER, reply_markup=menu_kb(u))
    else:
        u = dbm.get_user(uid)
        await m.answer(f"⏳ Raqamingiz ({u.get('phone')}) hali hech qaysi menejer tomonidan qo'shilmagan.\n\n"
                       "Menejeringizga ayting: sizni shu raqam bilan haydovchi sifatida qo'shsin. "
                       "Qo'shilishi bilan sizga xabar keladi va hujjatlaringiz shu yerda ko'rinadi.",
                       reply_markup=menu_kb(u))


@dp.callback_query(F.data.startswith("role:"))
async def cb_role(cb: CallbackQuery, u: dict):
    uid = cb.from_user.id
    if not u.get("phone"):
        await cb.answer("Avval raqamingizni yuboring: /start", show_alert=True)
        return
    role = cb.data[5:]
    await core.safe_edit(cb.message, "👔 Menejer tanlandi." if role == "manager" else "🚛 Haydovchi tanlandi.")
    await cb.answer()
    if u.get("role") == "driver" and u.get("manager_id") and u.get("driver_rec_id"):
        dbm.unlink_driver(u["manager_id"], u["driver_rec_id"])
    if role == "manager":
        dbm.set_user(uid, role="manager", manager_id=None, driver_rec_id=None)
        days = dbm.setting_int("trial_days", 10)
        until = dbm.start_trial(uid, days)
        u = dbm.get_user(uid)
        if until:
            await cb.message.answer(
                f"🎉 Xush kelibsiz! Sizga {days} kunlik BEPUL sinov berildi ({dbm.fmt_dt(until, True)} gacha) - "
                "barcha imkoniyatlar ochiq.\n\nBoshlash uchun:\n1️⃣ Fura yoki haydovchi hujjati rasmini yuboring\n"
                "2️⃣ «📍 GPS» orqali GPS ni ulang\n3️⃣ «🔎 Yuklarni ko'rish» orqali yuk qidiring\n\n"
                "Yoki shunchaki yozing - men tushunaman 🤖", reply_markup=menu_kb(u))
        else:
            await cb.message.answer("👔 Menejer rejimi.", reply_markup=menu_kb(u))
            if not dbm.has_access(u):
                await cb.message.answer(paywall_text(u), reply_markup=paywall_kb())
    else:
        await _become_driver(cb.message, uid)


# ====================================================================== BUYRUQLAR

@dp.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"Sizning Telegram ID: {m.from_user.id}")


@dp.message(Command("help"))
@dp.message(F.text == BTN_HELP)
async def cmd_help(m: Message, u: dict):
    sup = dbm.setting("support")
    tail = f"\n\n🆘 Yordam: {sup}" if sup else ""
    await m.answer((HELP_DRIVER if u.get("role") == "driver" else HELP_MANAGER) + tail)


@dp.message(Command("menu"))
async def cmd_menu(m: Message, u: dict):
    await m.answer("Asosiy menyu 👇", reply_markup=menu_kb(u))


@dp.message(Command("cancel"))
async def cmd_cancel(m: Message):
    w = core.WAIT.pop(m.from_user.id, None)
    if w:
        if w.get("k") == "login":
            await userbot.login_cancel(m.from_user.id)
        await m.answer("Bekor qilindi.")
    else:
        await m.answer("Bekor qiladigan narsa yo'q.")


@dp.message(Command("admin"))
@dp.message(F.text == BTN_ADMIN)
async def cmd_admin(m: Message):
    if not dbm.is_admin(m.from_user.id):
        return
    await admin.show_home(m, m.from_user.id)


# ====================================================================== MENEJER TUGMALARI

@dp.message(F.text == BTN_VEHICLES)
async def btn_vehicles(m: Message, u: dict):
    if not await need_manager(m, u):
        return
    text, k = vehicle_list_view(m.from_user.id)
    await m.answer(text, reply_markup=k)


@dp.message(F.text == BTN_DRIVERS)
async def btn_drivers(m: Message, u: dict):
    if not await need_manager(m, u):
        return
    text, k = driver_list_view(m.from_user.id)
    await m.answer(text, reply_markup=k)


@dp.message(F.text == BTN_TABLE)
async def btn_table(m: Message, u: dict):
    if not await need_manager(m, u):
        return
    await m.bot.send_chat_action(m.chat.id, "upload_photo")
    await send_tables(m.chat.id, m.from_user.id)


@dp.message(F.text == BTN_EXPIRING)
async def btn_expiring(m: Message, u: dict):
    if not await need_manager(m, u):
        return
    rows = dbm.expiring(m.from_user.id, within_days=config.YELLOW_DAYS)
    if not rows:
        await m.answer(f"✅ {config.YELLOW_DAYS} kun ichida muddati tugaydigan yoki o'tgan hujjat yo'q.")
        return
    lines = []
    for r in rows:
        d = r["days_left"]
        st = f"MUDDATI O'TGAN ({-d} kun)" if d < 0 else f"{d} kun qoldi"
        e = "🔴" if d <= config.RED_DAYS else "🟡"
        lines.append(f"{e} {r['owner_icon']} {r['owner_name']} - {r['doc_label']}\n    {dbm.fmt_date(r['expiry_date'])} ({st})")
    await send_long(m, f"⚠️ Muddati yaqin hujjatlar ({len(rows)} ta):\n\n" + "\n\n".join(lines))


@dp.message(F.text == BTN_REMINDERS)
async def btn_reminders(m: Message, u: dict):
    if not await need_manager(m, u):
        return
    rems = dbm.list_reminders(m.from_user.id)
    if not rems:
        await m.answer("Hozircha eslatma yo'q.\nMasalan: «25.11.2026 da O'zbekistonga kirish navbati olish eslat» deb yozing.")
        return
    for r in rems[:20]:
        text = f"🔔 {dbm.reminder_title(r)}\n"
        if r.get("plate"):
            text += f"🚛 {r['plate']}\n"
        text += f"📅 {dbm.fmt_date(r['remind_date'])}"
        await m.answer(text, reply_markup=K(inline_keyboard=[[B(text="✅ Bajarildi", callback_data=f"rd:{r['id']}")]]))


@dp.message(F.text == BTN_EXCEL)
async def btn_excel(m: Message, u: dict):
    if not await need_manager(m, u):
        return
    data = await asyncio.to_thread(tables.excel_bytes, m.from_user.id)
    await m.answer_document(BufferedInputFile(data, "logistika_hisobot.xlsx"),
                            caption="📥 Furalar, haydovchilar va eslatmalar (rangli).")


# ---------------------------------------------------------------------- GPS

def gps_menu(u):
    if gps.client_for_user(u):
        return None
    pending = [r for r in dbm.gps_req_pending() if r["user_id"] == u["user_id"]]
    if pending:
        return ("⏳ GPS ulash so'rovingiz admin tomonidan ko'rib chiqilmoqda. Tayyor bo'lishi bilan xabar keladi.",
                K(inline_keyboard=[[B(text="🔁 Qaytadan yuborish", callback_data="gps:connect")]]))
    return ("📍 GPS hali ulanmagan.\n\nGPS (Wialon / gpsmonitor.uz) dagi login va parolingizni yuboring - admin "
            "ulab beradi. Yoki tokeningiz bo'lsa, o'zingiz kiriting.",
            K(inline_keyboard=[[B(text="🔗 GPS ulash (login/parol)", callback_data="gps:connect")],
                               [B(text="🔑 Menda token bor", callback_data="gps:token")]]))


@dp.message(F.text == BTN_GPS)
async def btn_gps(m: Message, u: dict):
    if not await need_manager(m, u):
        return
    menu = gps_menu(u)
    if menu:
        await m.answer(menu[0], reply_markup=menu[1])
        return
    await m.bot.send_chat_action(m.chat.id, "typing")
    w = gps.client_for_user(u)
    try:
        units = await gps.get_units(w)
    except Exception:
        logging.exception("GPS xatosi")
        await m.answer("❌ GPS tizimiga ulanib bo'lmadi. Birozdan keyin qayta urinib ko'ring yoki tokenni yangilang.",
                       reply_markup=K(inline_keyboard=[[B(text="🔗 Qayta ulash", callback_data="gps:connect")]]))
        return
    if not units:
        await m.answer("GPS tizimida mashina topilmadi.")
        return
    await send_long(m, gps.fleet_text(units), disable_web_page_preview=True)
    await m.answer("Qo'shimcha:", reply_markup=K(inline_keyboard=[
        [B(text="🛣 Bugun kim necha km yurdi", callback_data="gps:km")],
        [B(text="🔌 GPS ni uzish", callback_data="gps:off")]]))


@dp.callback_query(F.data.startswith("gps:"))
async def cb_gps(cb: CallbackQuery, u: dict):
    if not await need_manager(cb, u):
        return
    uid, act = cb.from_user.id, cb.data[4:]
    if act == "connect":
        core.WAIT[uid] = {"k": "gps:server"}
        await cb.message.answer("🌐 1/3. GPS server manzilini yuboring yoki tugmani bosing:",
                                reply_markup=K(inline_keyboard=[[B(text=f"✅ {config.GPS_DEFAULT_SERVER}",
                                                                   callback_data="gps:srvdef")]]))
    elif act == "srvdef":
        core.WAIT[uid] = {"k": "gps:login", "server": config.GPS_DEFAULT_SERVER}
        await cb.message.answer(f"🌐 Server: {config.GPS_DEFAULT_SERVER}\n👤 2/3. Endi LOGIN ni yuboring:")
    elif act == "token":
        core.WAIT[uid] = {"k": "gps:tok"}
        await cb.message.answer(f"🔑 Wialon tokeningizni yuboring.\n(Server {config.GPS_DEFAULT_SERVER} emas bo'lsa: "
                                "«server token» ko'rinishida)")
    elif act == "km":
        await cb.answer("Hisoblanmoqda...")
        w = gps.client_for_user(u)
        try:
            rep = await gps.fleet_mileage(w, await gps.get_units(w), dbm.today())
        except Exception as e:
            await cb.message.answer(f"❌ GPS xatosi: {e}")
            return
        lines = [f"🛣 {rep['sana']} - yurgan masofa:\n"]
        for r in rep["mashinalar"]:
            lines.append(f"🚛 {r['fura']}: {r['km'] if r['km'] is not None else '?'} km"
                         + (f" ({r['yurgan_vaqti']})" if r.get("yurgan_vaqti") else ""))
        lines.append(f"\nJami: {rep['jami_km']} km (GPS nuqtalaridan, taxminiy)")
        await send_long(cb.message, "\n".join(lines))
        return
    elif act == "off":
        dbm.set_user(uid, gps_token=None)
        await cb.message.answer("🔌 GPS uzildi.")
    await cb.answer()


async def gps_input(m: Message, w: dict, u: dict):
    uid, text, k = m.from_user.id, (m.text or "").strip(), w["k"]
    if k == "gps:server":
        if len(text) < 4 or "." not in text:
            await m.answer("Server manzili noto'g'ri. Masalan: https://1.gpsmonitor.uz")
            return
        w.update(k="gps:login", server=text)
        await m.answer("👤 2/3. Endi LOGIN ni yuboring:")
    elif k == "gps:login":
        w.update(k="gps:pass", login=text)
        await m.answer("🔑 3/3. Endi PAROL ni yuboring (xabaringiz darhol o'chiriladi):")
    elif k == "gps:pass":
        core.WAIT.pop(uid, None)
        try:
            await m.delete()
        except Exception:
            pass
        rid = dbm.gps_req_add(uid, w["server"], w["login"], core.enc(text))
        await admin.notify_gps_request(rid)
        await m.answer("✅ Ma'lumotlar adminga yuborildi.\n⏳ GPS ulanishini kuting - tayyor bo'lishi bilan sizga xabar keladi "
                       "(odatda bir necha soat ichida).")
    elif k == "gps:tok":
        parts = text.split()
        server, token = (parts[0], parts[1]) if len(parts) == 2 else (config.GPS_DEFAULT_SERVER, parts[-1] if parts else "")
        if len(token) < 20:
            await m.answer("Token juda qisqa. To'liq yuboring yoki /cancel.")
            return
        try:
            await m.delete()
        except Exception:
            pass
        ok, info = await gps.check_token(server, token)
        if not ok:
            await m.answer(f"❌ Token ishlamadi: {info}\nQayta yuboring yoki /cancel.")
            return
        core.WAIT.pop(uid, None)
        dbm.set_user(uid, gps_url=server, gps_token=core.enc(token))
        await m.answer(f"✅ GPS muvaffaqiyatli ulandi ({info})! «📍 GPS» tugmasini bosing.")


# ---------------------------------------------------------------------- YUK QIDIRISH

CARGO_HINT = """🔎 Yuk yoki fura qidirish

Qayerdan qayerga kerakligini yozing, masalan:
• «Toshkent Moskva yuk kerak» - furangiz bor, YUK qidiryapsiz
• «Andijon Almati fura kerak» - yukingiz bor, MASHINA qidiryapsiz
• «Samarqanddan Rossiyaga tent yuk, 3000$ dan qimmat» - shartlar bilan

Bot yuk guruhlaridagi oxirgi 24 soatlik e'lonlarni tekshiradi va mos e'lonlarni ko'rsatadi. Xohlasangiz, e'lon egalari bilan o'zi gaplashib, narx va shartlarni aniqlab beradi."""


@dp.message(F.text == BTN_CARGO)
async def btn_cargo(m: Message, u: dict):
    if not await need_manager(m, u):
        return
    core.WAIT[m.from_user.id] = {"k": "cargo:q"}
    await m.answer(CARGO_HINT, reply_markup=K(inline_keyboard=[[B(text="📦 Qidiruvlarim holati", callback_data="cg:status")]]))


async def cargo_input(m: Message):
    uid = m.from_user.id
    q = await ai.parse_cargo_query(m.text or "")
    if not q.get("origin"):
        await m.answer("Qayerdan ekanini tushunmadim. Masalan: «Toshkent Moskva yuk kerak»")
        return
    core.WAIT.pop(uid, None)
    res = cargo.start_search(uid, q["origin"], q.get("destination") or "", q.get("note") or "", None,
                             q.get("mode") or "cargo")
    if res.get("xato"):
        await m.answer("❌ " + res["xato"])
        return
    what = "yuk" if (q.get("mode") or "cargo") == "cargo" else "bo'sh fura"
    await m.answer(f"🔎 Qidirilmoqda: {q['origin']} → {q.get('destination') or 'istalgan manzil'} ({what})\n"
                   "Guruhlar tekshirilmoqda, natija 1-3 daqiqada keladi...")


@dp.callback_query(F.data.startswith("cg:"))
async def cb_cargo(cb: CallbackQuery, u: dict):
    if not await need_manager(cb, u):
        return
    uid = cb.from_user.id
    parts = cb.data.split(":")
    if parts[1] == "status":
        await cb.answer()
        await send_long(cb.message, cargo.status_text(uid))
        return
    rid = int(parts[2])
    if not dbm.get_request(uid, rid):
        await cb.answer("Topilmadi", show_alert=True)
        return
    if parts[1] == "yes":
        ok = cargo.start_outreach(uid, rid)
        await core.safe_edit(cb.message, "🤖 Boshladim! E'lon egalariga birma-bir yozaman (ban bo'lmasligi uchun oraliq bilan). "
                                         "Har bir suhbat tugagach sizga xulosa va kontaktlarni yuboraman." if ok else
                             "⏳ Yozish allaqachon davom etmoqda.")
    else:
        await core.safe_edit(cb.message, "👍 Yaxshi. Yuqoridagi havolalar va @username lar orqali o'zingiz yozishingiz mumkin.")
    await cb.answer()


@dp.callback_query(F.data.startswith("cs:"))
async def cb_continue_outreach(cb: CallbackQuery, u: dict):
    if not await need_manager(cb, u):
        return
    rid = int(cb.data[3:])
    if not dbm.get_request(cb.from_user.id, rid):
        await cb.answer("Topilmadi", show_alert=True)
        return
    ok = cargo.start_outreach(cb.from_user.id, rid)
    await cb.answer("Yozish boshlandi" if ok else "Yozish allaqachon davom etmoqda", show_alert=not ok)


@dp.callback_query(F.data.startswith("ldh:"))
async def cb_lead_history(cb: CallbackQuery):
    lead = dbm.get_lead(int(cb.data[4:]))
    if not lead or lead["user_id"] != cb.from_user.id:
        await cb.answer("Topilmadi", show_alert=True)
        return
    await cb.answer()
    await send_long(cb.message, "📜 Suhbat:\n\n" + cargo.transcript(lead["id"]))


@dp.callback_query(F.data.startswith("ldc:"))
async def cb_lead_close(cb: CallbackQuery):
    _, lid, st = cb.data.split(":")
    lead = dbm.get_lead(int(lid))
    if not lead or lead["user_id"] != cb.from_user.id or st not in ("won", "closed"):
        await cb.answer("Topilmadi", show_alert=True)
        return
    dbm.update_lead(lead["id"], status=st)
    await core.safe_edit(cb.message, (cb.message.text or "") + ("\n\n✅ Kelishildi deb belgilandi" if st == "won"
                                                               else "\n\n🗄 Yopildi"))
    await cb.answer()


# ---------------------------------------------------------------------- AVTO XABAR

@dp.message(F.text == BTN_AP)
async def btn_ap(m: Message, u: dict):
    if not await need_manager(m, u):
        return
    text, k = autopost.menu(m.from_user.id)
    await m.answer(text, reply_markup=k)


async def ap_show(msg: Message, uid: int, edit=False):
    text, k = autopost.menu(uid)
    if edit:
        await core.safe_edit(msg, text, k)
    else:
        await msg.answer(text, reply_markup=k)


@dp.callback_query(F.data.startswith("ap:"))
async def cb_ap(cb: CallbackQuery, u: dict):
    if not await need_manager(cb, u):
        return
    uid = cb.from_user.id
    p = cb.data.split(":")
    act = p[1]
    alert = None
    acc = dbm.user_account(uid)
    if act == "connect":
        if not config.TG_API_ID or not config.TG_API_HASH:
            alert = "Bu imkoniyat hozircha sozlanmagan (admin TG_API_ID ni kiritishi kerak)."
        else:
            await login.begin(cb.message, uid, "user")
    elif act == "groups":
        if not acc or not userbot.is_connected(acc["id"]):
            alert = "Avval akkauntni ulang."
        else:
            await pickers.show(cb.message, uid, "a", acc["id"], 0, refresh=True)
    elif act == "text":
        core.WAIT[uid] = {"k": "ap:text"}
        await cb.message.answer("✏️ Guruhlarga yuboriladigan e'lon matnini yozing.\nMasalan: «Assalomu alaykum! "
                                "Toshkentda tent fura bor, 20 t, Rossiya yo'nalishiga yuk kerak. Tel: +998...»")
    elif act == "int":
        await core.safe_edit(cb.message, "⏱ Har necha daqiqada yuborilsin?", autopost.interval_kb())
    elif act == "iv":
        n = int(p[2])
        if n in config.AP_INTERVALS:
            dbm.ap_update(uid, interval_min=n)
        await ap_show(cb.message, uid, edit=True)
    elif act == "start":
        err = autopost.start(uid)
        alert = err or "▶️ Avto xabar ishga tushdi!"
        await ap_show(cb.message, uid, edit=True)
    elif act == "stop":
        autopost.stop(uid)
        alert = "⏸ To'xtatildi"
        await ap_show(cb.message, uid, edit=True)
    elif act == "stat":
        await cb.message.answer(autopost.report_text(uid))
    elif act == "menu":
        await ap_show(cb.message, uid, edit=True)
    elif act == "off":
        await core.safe_edit(cb.message, "🔌 Akkaunt uzilsinmi? Avto xabar to'xtaydi va bot sizning akkauntingizdan chiqadi.",
                             K(inline_keyboard=[[B(text="✅ Ha, uzish", callback_data="ap:off2"),
                                                 B(text="❌ Yo'q", callback_data="ap:menu")]]))
    elif act == "off2":
        autopost.stop(uid)
        if acc:
            await userbot.remove(acc["id"], logout=True)
        await ap_show(cb.message, uid, edit=True)
        alert = "Akkaunt uzildi"
    try:
        await cb.answer(alert, show_alert=bool(alert and len(alert) > 30))
    except Exception:
        pass


async def ap_text_input(m: Message):
    uid = m.from_user.id
    text = (m.text or "").strip()
    if len(text) < 10:
        await m.answer("Matn juda qisqa. To'liqroq yozing yoki /cancel.")
        return
    if len(text) > 3500:
        await m.answer("Matn juda uzun (3500 belgidan oshmasin).")
        return
    chk = await ai.check_ad_text(text)
    if not chk.get("ok", True):
        await m.answer(f"❌ Bu matnni yuborib bo'lmaydi: {chk.get('reason') or 'qoidalarga mos emas'}\nBoshqa matn yozing.")
        return
    core.WAIT.pop(uid, None)
    dbm.ap_update(uid, text=text)
    tip = f"\n💡 Maslahat: {chk['tip']}" if chk.get("tip") else ""
    await m.answer("✅ Matn saqlandi." + tip)
    await ap_show(m, uid)


# ---------------------------------------------------------------------- GURUH TANLASH (umumiy)

def _picker_allowed(uid, scope, aid) -> bool:
    if scope == "p":
        return dbm.is_admin(uid)
    a = dbm.acc_get(aid)
    return bool(a and a["kind"] == "user" and a["owner_id"] == uid)


@dp.callback_query(F.data.regexp(r"^gp[pacd]?:[pa]:"))
async def cb_picker(cb: CallbackQuery):
    uid = cb.from_user.id
    parts = cb.data.split(":")
    kind, scope, aid = parts[0], parts[1], int(parts[2])
    if not _picker_allowed(uid, scope, aid):
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    note = None
    if kind == "gp":
        note = pickers.toggle(uid, scope, aid, int(parts[3]))
        await pickers.show(cb.message, uid, scope, aid, int(parts[4]), edit=True)
    elif kind == "gpp":
        await pickers.show(cb.message, uid, scope, aid, int(parts[3]), edit=True)
    elif kind == "gpa":
        pickers.select_all(uid, scope, aid)
        await pickers.show(cb.message, uid, scope, aid, int(parts[3]), edit=True)
    elif kind == "gpc":
        pickers.clear(uid, scope, aid)
        await pickers.show(cb.message, uid, scope, aid, int(parts[3]), edit=True)
    elif kind == "gpd":
        n = len(pickers.selected_ids(uid, scope, aid))
        await core.safe_edit(cb.message, f"✅ Saqlandi: {n} ta guruh tanlangan.")
        if scope == "p":
            await admin.show_account(cb.message, aid)
        else:
            camp = dbm.ap_get(uid)
            if not (camp and camp.get("text")):
                core.WAIT[uid] = {"k": "ap:text"}
                await cb.message.answer("✏️ Endi guruhlarga yuboriladigan e'lon matnini yozing:")
            else:
                await ap_show(cb.message, uid)
    try:
        await cb.answer(note)
    except Exception:
        pass


@dp.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


# ---------------------------------------------------------------------- OBUNA VA TO'LOV

@dp.message(F.text == BTN_SUB)
async def btn_sub(m: Message, u: dict):
    if not await need_registered(m, u):
        return
    price, days = dbm.setting_int("price", 100000), dbm.setting_int("period_days", 30)
    st, _ = dbm.sub_state(u)
    text = f"💳 Obuna\n\nHolat: {sub_line(u)}\n💰 Narx: {core.money(price)} so'm / {days} kun"
    paid = dbm.list_payments(m.from_user.id, 3)
    if paid:
        text += "\n\nOxirgi to'lovlar:\n" + "\n".join(f"✅ {dbm.fmt_dt(p['paid_at'])} - {core.money(p['amount'])} so'm"
                                                       for p in paid)
    rows = []
    if u.get("role") != "driver" and st not in ("admin", "vip"):
        rows.append([B(text="💳 To'lov qilish" if st != "paid" else "💳 Oldindan uzaytirish", callback_data="pay:info")])
    sup = dbm.setting("support")
    if sup:
        rows.append([B(text="🆘 Yordam", url=f"https://t.me/{sup.lstrip('@')}")])
    await m.answer(text, reply_markup=K(inline_keyboard=rows) if rows else None)


@dp.callback_query(F.data.startswith("pay:"))
async def cb_pay(cb: CallbackQuery, u: dict):
    uid = cb.from_user.id
    parts = cb.data.split(":")
    act = parts[1]
    if not u.get("role"):
        await cb.answer("Avval ro'yxatdan o'ting: /start", show_alert=True)
        return
    if act == "info":
        await cb.message.answer(payments.explain_text(), reply_markup=payments.explain_kb())
    elif act == "go":
        await cb.answer("⏳")
        err = await payments.start(uid, cb.message.chat.id)
        if err:
            await cb.message.answer(err)
        return
    elif act == "later":
        try:
            await cb.message.delete()
        except Exception:
            pass
    elif act == "cancel":
        await payments.cancel(uid, int(parts[2]))
    await cb.answer()


# ---------------------------------------------------------------------- PROFIL

@dp.message(F.text == BTN_PROFILE)
async def btn_profile(m: Message, u: dict):
    if not await need_registered(m, u):
        return
    role = {"manager": "👔 Menejer", "driver": "🚛 Haydovchi"}.get(u.get("role"), "-")
    lines = [f"👤 {u.get('name') or '-'}", f"🆔 {u['user_id']}", f"📞 {u.get('phone') or '-'}", f"Rol: {role}",
             f"Obuna: {sub_line(u)}"]
    rows = []
    if u.get("role") == "manager":
        lines.append(f"🏢 Kompaniya: {u.get('company') or '-'} (yuk egalari bilan suhbatda shu nom ishlatiladi)")
        lines.append(f"📍 GPS: {'✅ ulangan' if gps.client_for_user(u) else '❌'}")
        rows.append([B(text="🏢 Kompaniya nomi", callback_data="prof:company")])
    elif u.get("manager_id"):
        mu = dbm.get_user(u["manager_id"]) or {}
        lines.append(f"🏢 Kompaniya: {mu.get('company') or mu.get('name') or '-'}")
    rows.append([B(text="🔄 Rolni almashtirish", callback_data="prof:role"),
                 B(text="🧹 Suhbatni tozalash", callback_data="prof:clear")])
    await m.answer("\n".join(lines), reply_markup=K(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("prof:"))
async def cb_profile(cb: CallbackQuery, u: dict):
    uid, act = cb.from_user.id, cb.data[5:]
    if act == "company":
        core.WAIT[uid] = {"k": "prof:company"}
        await cb.message.answer("🏢 Kompaniyangiz nomini yozing:")
    elif act == "role":
        await cb.message.answer("Qaysi rolga o'tasiz? (Menejer ma'lumotlaringiz o'chmaydi)\n\n" + role_text(),
                                reply_markup=role_kb())
    elif act == "clear":
        dbm.clear_history(uid)
        await cb.message.answer("🧹 AI bilan suhbat tarixi tozalandi.")
    await cb.answer()


# ====================================================================== HAYDOVCHI TUGMALARI

@dp.message(F.text == BTN_MYDOCS)
async def btn_mydocs(m: Message, u: dict):
    sc = await need_driver(m, u)
    if not sc:
        return
    mid, d, vs = sc
    await m.answer(driver_card(mid, d, for_manager=False))


@dp.message(F.text == BTN_MYTRUCK)
async def btn_mytruck(m: Message, u: dict):
    sc = await need_driver(m, u)
    if not sc:
        return
    mid, d, vs = sc
    if not vs:
        await m.answer("Sizga hali fura biriktirilmagan.")
        return
    for v in vs:
        await m.answer(vehicle_card(mid, v), reply_markup=K(inline_keyboard=[
            [B(text="📍 Joylashuvi", callback_data=f"dvg:{v['id']}")]]))


@dp.message(F.text == BTN_TRUCKLOC)
async def btn_truckloc(m: Message, u: dict):
    sc = await need_driver(m, u)
    if not sc:
        return
    mid, d, vs = sc
    if not vs:
        await m.answer("Sizga hali fura biriktirilmagan.")
        return
    for v in vs:
        await _send_vehicle_gps(m, u, v)


@dp.message(F.text == BTN_DREM)
async def btn_drem(m: Message, u: dict):
    sc = await need_driver(m, u)
    if not sc:
        return
    mid, d, vs = sc
    ids = {v["id"] for v in vs}
    rems = [r for r in dbm.list_reminders(mid) if r["vehicle_id"] in ids]
    if not rems:
        await m.answer("Hozircha eslatma yo'q.")
        return
    await m.answer("⏰ Eslatmalar:\n\n" + "\n".join(
        f"🔔 {dbm.reminder_title(r)}\n   🚛 {r['plate']} · 📅 {dbm.fmt_date(r['remind_date'])}" for r in rems))


async def _send_vehicle_gps(m: Message, u: dict, v: dict):
    w = gps.client_for_user(u)
    if not w:
        await m.answer("GPS ulanmagan.")
        return
    try:
        unit = gps.find_unit(await gps.get_units(w), v["plate"])
    except Exception:
        logging.exception("GPS xatosi")
        await m.answer("❌ GPS tizimiga ulanib bo'lmadi.")
        return
    if not unit:
        await m.answer(f"🤷 {v['plate']} GPS tizimida topilmadi. Wialon'dagi mashina nomida shu raqam bo'lishi kerak.")
        return
    await m.answer(gps.unit_text(unit), disable_web_page_preview=True)
    if unit["lat"] is not None:
        await m.answer_location(unit["lat"], unit["lon"])


@dp.callback_query(F.data.startswith("dvg:"))
async def cb_driver_vehicle_gps(cb: CallbackQuery, u: dict):
    sc = await need_driver(cb, u)
    if not sc:
        return
    mid, d, vs = sc
    v = next((x for x in vs if x["id"] == int(cb.data[4:])), None)
    await cb.answer()
    if v:
        await _send_vehicle_gps(cb.message, u, v)


# ====================================================================== MENEJER: INLINE TUGMALAR

@dp.callback_query(F.data == "vl")
async def cb_vehicle_list(cb: CallbackQuery, u: dict):
    if not await need_manager(cb, u):
        return
    text, k = vehicle_list_view(cb.from_user.id)
    await core.safe_edit(cb.message, text, k)
    await cb.answer()


@dp.callback_query(F.data == "dl")
async def cb_driver_list(cb: CallbackQuery, u: dict):
    if not await need_manager(cb, u):
        return
    text, k = driver_list_view(cb.from_user.id)
    await core.safe_edit(cb.message, text, k)
    await cb.answer()


@dp.callback_query(F.data.startswith("v:"))
async def cb_vehicle(cb: CallbackQuery, u: dict):
    if not await need_manager(cb, u):
        return
    uid, vid = cb.from_user.id, int(cb.data[2:])
    v = dbm.get_vehicle(uid, vid)
    if not v:
        await cb.answer("Fura topilmadi", show_alert=True)
        return
    k = K(inline_keyboard=[
        [B(text="📍 Joylashuvi (GPS)", callback_data=f"vg:{vid}")],
        [B(text="📎 Hujjat fayllari", callback_data=f"vf:{vid}")],
        [B(text="🔙 Furalar ro'yxati", callback_data="vl")]])
    await core.safe_edit(cb.message, vehicle_card(uid, v), k)
    await cb.answer()


def driver_kb(d):
    rows = [[B(text="📞 Telefon kiritish", callback_data=f"dph:{d['id']}"),
             B(text="📎 Hujjat fayllari", callback_data=f"df:{d['id']}")]]
    if d.get("tg_user_id"):
        rows.append([B(text="🚪 Botdan chiqarish", callback_data=f"dout:{d['id']}")])
    rows.append([B(text="🔙 Haydovchilar ro'yxati", callback_data="dl")])
    return K(inline_keyboard=rows)


@dp.callback_query(F.data.startswith("d:"))
async def cb_driver(cb: CallbackQuery, u: dict):
    if not await need_manager(cb, u):
        return
    uid, did = cb.from_user.id, int(cb.data[2:])
    d = dbm.get_driver(uid, did)
    if not d:
        await cb.answer("Haydovchi topilmadi", show_alert=True)
        return
    await core.safe_edit(cb.message, driver_card(uid, d), driver_kb(d))
    await cb.answer()


@dp.callback_query(F.data.startswith("dph:"))
async def cb_driver_phone(cb: CallbackQuery, u: dict):
    if not await need_manager(cb, u):
        return
    did = int(cb.data[4:])
    if not dbm.get_driver(cb.from_user.id, did):
        await cb.answer("Topilmadi", show_alert=True)
        return
    core.WAIT[cb.from_user.id] = {"k": "dph", "did": did}
    await cb.message.answer("📞 Haydovchining telefon raqamini yuboring (masalan +998901234567).\n"
                            "U shu raqam bilan botga kirsa - avtomatik ulanadi.")
    await cb.answer()


@dp.callback_query(F.data.startswith("dout"))
async def cb_driver_out(cb: CallbackQuery, u: dict):
    if not await need_manager(cb, u):
        return
    uid = cb.from_user.id
    confirm = cb.data.startswith("dout2:")
    did = int(cb.data.split(":")[1])
    d = dbm.get_driver(uid, did)
    if not d:
        await cb.answer("Topilmadi", show_alert=True)
        return
    if not confirm:
        await core.safe_edit(cb.message, f"🚪 {d['name']} botdan chiqarilsinmi?\nU endi hujjatlarni ko'rmaydi va eslatma "
                                         "olmaydi. Bazadagi yozuvi va hujjatlari saqlanadi.",
                             K(inline_keyboard=[[B(text="✅ Ha, chiqarish", callback_data=f"dout2:{did}"),
                                                 B(text="❌ Yo'q", callback_data=f"d:{did}")]]))
        await cb.answer()
        return
    tid = dbm.unlink_driver(uid, did)
    if tid:
        await core.send(tid, "ℹ️ Menejeringiz sizni botdagi kompaniya ro'yxatidan chiqardi.")
    d = dbm.get_driver(uid, did)
    await core.safe_edit(cb.message, "✅ Botdan chiqarildi.\n\n" + driver_card(uid, d), driver_kb(d))
    await cb.answer("Chiqarildi")


async def driver_phone_input(m: Message, w: dict):
    uid = m.from_user.id
    phone = dbm.digits(m.text)
    if len(phone) < 9:
        await m.answer("Raqam noto'g'ri. Masalan: +998901234567")
        return
    core.WAIT.pop(uid, None)
    res = dbm.update_driver(uid, w["did"], "phone", "+" + phone)
    if not res or res.get("error"):
        await m.answer("❌ Saqlab bo'lmadi.")
        return
    link = await ai.link_after_phone(uid, w["did"])
    d = dbm.get_driver(uid, w["did"])
    note = ("✅ Haydovchi botga ulandi!" if link.get("botga_ulandi") else "ℹ️ " + link.get("izoh", ""))
    await m.answer(f"✅ Telefon saqlandi.\n{note}\n\n" + driver_card(uid, d), reply_markup=driver_kb(d))


async def _send_owner_files(cb: CallbackQuery, owner_type: str, owner_id: int):
    uid = cb.from_user.id
    docs = dbm.owner_docs(uid, owner_type, owner_id)
    labels = config.VEHICLE_DOCS if owner_type == "vehicle" else config.DRIVER_DOCS
    count = 0
    for key, label in labels.items():
        r = docs.get(key)
        if r and r.get("file_id"):
            try:
                await send_stored_file(cb.message.chat.id, r["file_id"], r["file_kind"], label)
                count += 1
            except Exception:
                logging.exception("Faylni yuborib bo'lmadi")
    await cb.answer("Saqlangan fayl yo'q" if count == 0 else f"{count} ta fayl yuborildi", show_alert=(count == 0))


@dp.callback_query(F.data.startswith("vg:"))
async def cb_vehicle_gps(cb: CallbackQuery, u: dict):
    if not await need_manager(cb, u):
        return
    v = dbm.get_vehicle(cb.from_user.id, int(cb.data[3:]))
    if not v:
        await cb.answer("Fura topilmadi", show_alert=True)
        return
    if not gps.client_for_user(u):
        await cb.answer("GPS ulanmagan. «📍 GPS» tugmasi orqali ulang.", show_alert=True)
        return
    await cb.answer("GPS so'ralmoqda...")
    await _send_vehicle_gps(cb.message, u, v)


@dp.callback_query(F.data.startswith("vf:"))
async def cb_vehicle_files(cb: CallbackQuery, u: dict):
    if await need_manager(cb, u):
        await _send_owner_files(cb, "vehicle", int(cb.data[3:]))


@dp.callback_query(F.data.startswith("df:"))
async def cb_driver_files(cb: CallbackQuery, u: dict):
    if await need_manager(cb, u):
        await _send_owner_files(cb, "driver", int(cb.data[3:]))


@dp.callback_query(F.data.startswith("rd:"))
async def cb_reminder_done(cb: CallbackQuery):
    ok = dbm.mark_reminder_done(cb.from_user.id, int(cb.data[3:]))
    if ok:
        await core.safe_edit(cb.message, (cb.message.text or "") + "\n\n✅ Bajarildi")
    await cb.answer("Bajarildi" if ok else "Topilmadi")


# ====================================================================== FAYL QABUL QILISH (hujjat yoki to'lov cheki)

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf"}


@dp.message(F.photo | F.document)
async def on_file(m: Message, u: dict):
    uid = m.from_user.id
    p = dbm.open_payment(uid)
    if p and (p["status"] in ("pending", "checking") or
              (p["status"] in ("expired", "review") and not dbm.has_access(u))):
        await payments.handle_receipt(m, p)
        return
    if not await need_manager(m, u):
        return
    if m.photo:
        file_id, mime, kind = m.photo[-1].file_id, "image/jpeg", "photo"
    else:
        doc = m.document
        mime = (doc.mime_type or "").lower()
        if mime not in ALLOWED_MIME:
            await m.answer("Faqat rasm (JPG, PNG) yoki PDF qabul qilaman.")
            return
        if doc.file_size and doc.file_size > 20 * 1024 * 1024:
            await m.answer("Fayl juda katta (20 MB dan oshmasin).")
            return
        file_id, kind = doc.file_id, "document"

    wait = await m.answer("⏳ Hujjat o'qilmoqda...")
    try:
        buf = io.BytesIO()
        await bot.download(file_id, destination=buf)
        data = await ai.extract_document(buf.getvalue(), mime, hint=(m.caption or "").strip())
    except Exception:
        logging.exception("Hujjatni o'qishda xato")
        await wait.edit_text("❌ Hujjatni o'qib bo'lmadi. Aniqroq rasm yoki PDF yuborib ko'ring.")
        return

    dt = data.get("doc_type")
    ot = owner_type_of(dt)
    if not ot:
        await wait.edit_text("🤔 Bu qaysi hujjat ekanini aniqlay olmadim. Rasmga izoh yozib qayta yuboring "
                             "(masalan: «01928WNA sug'urta Qozog'iston»).")
        return
    data["expiry_date"] = dbm.parse_date(data.get("expiry_date"))
    data["issue_date"] = dbm.parse_date(data.get("issue_date"))
    plate = dbm.norm_plate(data.get("plate"))
    dname = dbm.norm_name(data.get("driver_name"))
    if ot == "vehicle" and not plate:
        await wait.edit_text("🚛 Bu fura hujjati, lekin fura raqami ko'rinmadi. Rasmga izoh qilib fura raqamini "
                             "yozib qayta yuboring (masalan: 01928WNA).")
        return
    if ot == "driver" and not dname:
        await wait.edit_text("👤 Bu haydovchi hujjati, lekin ism ko'rinmadi. Rasmga izoh qilib ismini yozib qayta yuboring.")
        return

    data["plate"], data["driver_name"] = plate, dname
    label = config.ALL_DOCS[dt]
    lines = ["📄 Hujjat aniqlandi", "", f"Turi: {label}"]
    if ot == "vehicle":
        lines.append(f"🚛 Fura: {plate}" + (f" ({data['vehicle_model']})" if data.get("vehicle_model") else ""))
        if data.get("vin"):
            lines.append(f"VIN: {data['vin']}")
    else:
        lines.append(f"👤 Haydovchi: {dname}")
    if data.get("doc_number"):
        lines.append(f"Raqami: {data['doc_number']}")
    if data["issue_date"]:
        lines.append(f"Berilgan sana: {dbm.fmt_date(data['issue_date'])}")
    if data["expiry_date"]:
        d = dbm.days_left(data["expiry_date"])
        note = f"{d} kun qoldi" if d >= 0 else f"MUDDATI O'TGAN ({-d} kun)"
        lines.append(f"Amal qilish muddati: {dbm.fmt_date(data['expiry_date'])} ({note})")
    elif dt not in config.NO_EXPIRY_TYPES:
        lines.append("⚠️ Amal qilish muddati topilmadi. Saqlagandan keyin menga yozib kiritishingiz mumkin.")
    if data.get("issuer"):
        lines.append(f"Bergan: {data['issuer']}")
    if data.get("confidence") in ("low", "medium"):
        lines.append("⚠️ Hujjat sifati past bo'lishi mumkin, ma'lumotlarni diqqat bilan tekshiring.")
    lines.append("\nSaqlaymizmi?")

    token = uuid.uuid4().hex[:10]
    if len(PENDING) > 1000:
        PENDING.clear()
    PENDING[token] = {"uid": uid, "data": data, "file_id": file_id, "kind": kind}
    await wait.edit_text("\n".join(lines), reply_markup=K(inline_keyboard=[[
        B(text="✅ Saqlash", callback_data=f"ok:{token}"), B(text="❌ Bekor qilish", callback_data=f"no:{token}")]]))


@dp.callback_query(F.data.startswith("ok:"))
async def cb_save(cb: CallbackQuery):
    p = PENDING.pop(cb.data[3:], None)
    if not p or p["uid"] != cb.from_user.id:
        await cb.answer("So'rov eskirgan. Hujjatni qayta yuboring.", show_alert=True)
        return
    uid, d = p["uid"], p["data"]
    dt = d["doc_type"]
    ot = owner_type_of(dt)
    if ot == "vehicle":
        oid = dbm.get_or_create_vehicle(uid, d["plate"], d.get("vehicle_model"), d.get("vin"))
    else:
        oid = dbm.get_or_create_driver(uid, d["driver_name"])
    doc_id = dbm.save_document(uid, ot, oid, dt, d.get("doc_number"), d["issue_date"], d["expiry_date"],
                               d.get("issuer"), d.get("notes"), p["file_id"], p["kind"])
    await core.safe_edit(cb.message, (cb.message.text or "").replace("\nSaqlaymizmi?", "") +
                         f"\n\n✅ Saqlandi (#{doc_id}). Xato bo'lsa, yozing, masalan: «muddatini 06.10.2027 ga o'zgartir».")
    await cb.answer("Saqlandi")
    # Tegishli haydovchiga ham xabar
    tid = None
    if ot == "driver":
        dr = dbm.get_driver(uid, oid)
        tid = dr.get("tg_user_id") if dr else None
    else:
        tid = dbm.driver_user_of_vehicle(uid, oid)
    if tid:
        await core.send(tid, f"📄 Yangi hujjat qo'shildi: {config.ALL_DOCS[dt]}"
                             + (f" - {dbm.fmt_date(d['expiry_date'])} gacha" if d.get("expiry_date") else ""))


@dp.callback_query(F.data.startswith("no:"))
async def cb_cancel(cb: CallbackQuery):
    PENDING.pop(cb.data[3:], None)
    await core.safe_edit(cb.message, "❌ Bekor qilindi.")
    await cb.answer()


# ====================================================================== MATN: KIRITISH HOLATLARI + AI SUHBAT

@dp.message(F.text)
async def on_text(m: Message, u: dict):
    uid = m.from_user.id
    w = core.WAIT.get(uid)
    if w:
        k = w.get("k", "")
        if k == "login":
            await login.step(m, w)
        elif k.startswith("admin:"):
            await admin.handle_input(m, w)
        elif k.startswith("gps:"):
            await gps_input(m, w, u)
        elif k == "cargo:q":
            await cargo_input(m)
        elif k == "ap:text":
            await ap_text_input(m)
        elif k == "dph":
            await driver_phone_input(m, w)
        elif k == "prof:company":
            core.WAIT.pop(uid, None)
            dbm.set_user(uid, company=(m.text or "").strip()[:80])
            await m.answer("✅ Kompaniya nomi saqlandi.")
        else:
            core.WAIT.pop(uid, None)
        return
    if (m.text or "").startswith("/"):
        await m.answer("Noma'lum buyruq. /help")
        return
    if not await need_registered(m, u):
        return
    if u.get("role") == "driver":
        if not await need_driver(m, u):
            return
    elif not await need_manager(m, u):
        return

    await m.bot.send_chat_action(m.chat.id, "typing")
    ctx = None
    try:
        answer, ctx = await ai.chat(u, m.text)
    except Exception:
        logging.exception("Suhbat xatosi")
        answer = "Xatolik yuz berdi. Birozdan keyin qayta urinib ko'ring."
    await send_long(m, answer)
    if not ctx:
        return
    if ctx.tables:
        await send_tables(m.chat.id, uid, tuple(sorted(ctx.tables)))
    if ctx.show_pay:
        await m.answer(payments.explain_text(), reply_markup=payments.explain_kb())
    if ctx.show_ap:
        await ap_show(m, uid)
    if ctx.show_gps_connect:
        menu = gps_menu(u) or ("📍 GPS allaqachon ulangan.", None)
        await m.answer(menu[0], reply_markup=menu[1])


# ====================================================================== ISHGA TUSHIRISH

_BG = []


async def main():
    if not config.BOT_TOKEN or not config.ANTHROPIC_API_KEY:
        raise SystemExit(".env da BOT_TOKEN va ANTHROPIC_API_KEY to'ldirilmagan.")
    if not config.SUPER_ADMINS:
        logging.warning("ALLOWED_USERS (bosh admin) ko'rsatilmagan - admin panelga hech kim kira olmaydi!")
    if not config.SESSION_KEY:
        logging.warning("SESSION_KEY yo'q - userbot sessiyalari va GPS tokenlar bazada SHIFRLANMAGAN holda saqlanadi!")
    dbm.init_db()
    dp.include_router(admin.router)
    await userbot.start_all()
    _BG.append(asyncio.create_task(reminders.reminder_loop()))
    _BG.append(asyncio.create_task(payments.loop()))
    _BG.append(asyncio.create_task(autopost.loop()))
    try:
        await bot.set_my_commands([BotCommand(command="start", description="Boshlash / asosiy menyu"),
                                   BotCommand(command="help", description="Yordam"),
                                   BotCommand(command="id", description="Mening Telegram ID im"),
                                   BotCommand(command="cancel", description="Joriy amalni bekor qilish")])
    except Exception:
        pass
    logging.info("Bot ishga tushdi")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
