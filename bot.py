"""Logistika Telegram boti.
Ishga tushirish:  python bot.py
"""
import asyncio
import io
import logging
import uuid

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup)

import ai
import config
import database as dbm
import reminders
import tables

logging.basicConfig(level=logging.INFO)
bot = Bot(config.BOT_TOKEN or "0:missing")
dp = Dispatcher()

BTN_VEHICLES = "🚛 Furalarim"
BTN_DRIVERS = "👤 Haydovchilarim"
BTN_TABLE = "📊 Umumiy jadval"
BTN_EXPIRING = "⚠️ Muddati yaqinlar"
BTN_REMINDERS = "⏰ Eslatmalarim"
BTN_EXCEL = "📥 Excel hisobot"
BTN_HELP = "ℹ️ Yordam"

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_VEHICLES), KeyboardButton(text=BTN_DRIVERS)],
        [KeyboardButton(text=BTN_TABLE), KeyboardButton(text=BTN_EXPIRING)],
        [KeyboardButton(text=BTN_REMINDERS), KeyboardButton(text=BTN_EXCEL)],
        [KeyboardButton(text=BTN_HELP)],
    ],
    resize_keyboard=True,
)

HELP_TEXT = """ℹ️ Men logistika yordamchisiman.

📄 Hujjat qo'shish: hujjatning rasmini yoki PDF faylini yuboring. Men uni o'qib, turini aniqlayman va tasdiqlashingizni so'rayman. Fura raqami hujjatda ko'rinmasa, rasmga izoh qilib yozing (masalan: 01928WNA).

💬 Menga oddiy yozib so'rang:
• Eng kam muddati qolgan fura qaysi?
• 01928WNA ning sug'urtasi qachon tugaydi?
• Bu oy muddati tugaydigan hujjatlarni ko'rsat
• Jadval ber
• Aliyev Vali ni 01928WNA ga biriktir
• 01928WNA ning sug'urta muddatini 06.10.2027 ga o'zgartir

🇨🇳 Xitoy navbati: «01928WNA Xitoydan 20.11.2026 ga navbat oldi» deb yozing. Men Xitoyga kirish va (3 kundan keyin) Rossiyaga chiqish navbatini olishni eslataman.
📌 Qo'lda eslatma: «25.11.2026 da O'zbekistonga kirish navbati olish eslat», «30.11.2026 da Akfa kompaniyasi to'lovi».

Rang belgilari: 🟢 30 kundan ko'p · 🟡 30 kungacha · 🔴 7 kun va kam yoki o'tgan · 🔵 muddatsiz · ⚪ hujjat yo'q"""

# Tasdiqlashni kutayotgan hujjatlar (bot qayta ishga tushsa, tozalanadi)
PENDING: dict = {}


# ====================================================================== RUXSAT

class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if config.ALLOWED_USERS and (user is None or user.id not in config.ALLOWED_USERS):
            uid = user.id if user else "?"
            if isinstance(event, Message):
                await event.answer(f"Kechirasiz, sizga ruxsat berilmagan.\nSizning ID: {uid}")
            elif isinstance(event, CallbackQuery):
                await event.answer("Ruxsat yo'q", show_alert=True)
            return None
        if user:
            dbm.ensure_user(user.id, user.full_name)
        return await handler(event, data)


dp.message.outer_middleware(AccessMiddleware())
dp.callback_query.outer_middleware(AccessMiddleware())


# ====================================================================== YORDAMCHI FUNKSIYALAR

async def send_long(m: Message, text: str, **kw):
    for i in range(0, len(text), 4000):
        await m.answer(text[i:i + 4000], **kw)


async def safe_edit(msg: Message, text: str, kb=None):
    try:
        await msg.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass  # matn o'zgarmagan bo'lsa, Telegram xato beradi - e'tibor bermaymiz


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


def driver_card(uid: int, d: dict) -> str:
    docs = dbm.owner_docs(uid, "driver", d["id"])
    cars = dbm.vehicles_of_driver(uid, d["id"])
    lines = [f"👤 {d['name']}"]
    if d["phone"]:
        lines.append(f"📞 {d['phone']}")
    lines.append("🚛 Fura: " + (", ".join(c["plate"] for c in cars) if cars else "biriktirilmagan"))
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
        rows.append([InlineKeyboardButton(text=title[:60], callback_data=f"v:{v['id']}")])
    return f"🚛 Furalaringiz ({len(vs)} ta). Birini tanlang:", InlineKeyboardMarkup(inline_keyboard=rows)


def driver_list_view(uid: int):
    ds = dbm.list_drivers(uid)
    if not ds:
        return ("Hozircha haydovchi yo'q. Haydovchi hujjatini (prava, pasport, viza...) rasm yoki PDF "
                "qilib yuboring, haydovchi o'zi qo'shiladi."), None
    by = dbm.docs_by_owner(uid, "driver")
    rows = [[InlineKeyboardButton(text=f"{worst_emoji(by.get(d['id'], {}))} {d['name']}"[:60],
                                  callback_data=f"d:{d['id']}")] for d in ds]
    return f"👤 Haydovchilaringiz ({len(ds)} ta). Birini tanlang:", InlineKeyboardMarkup(inline_keyboard=rows)


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


# ====================================================================== BUYRUQLAR VA TUGMALAR

@dp.message(CommandStart())
async def cmd_start(m: Message):
    await m.answer(
        "Salom! 👋 Men logistika yordamchisiman.\n\n"
        "Fura va haydovchi hujjatlarining rasmini yoki PDF faylini yuboring, men o'qib bazaga saqlayman "
        "va muddati tugashidan oldin eslatib turaman. Savollaringizni oddiy yozib so'rashingiz mumkin.\n\n"
        "Pastdagi tugmalardan foydalaning 👇",
        reply_markup=MAIN_KB)


@dp.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"Sizning Telegram ID: {m.from_user.id}")


@dp.message(Command("help"))
@dp.message(F.text == BTN_HELP)
async def cmd_help(m: Message):
    await m.answer(HELP_TEXT)


@dp.message(F.text == BTN_VEHICLES)
async def btn_vehicles(m: Message):
    text, kb = vehicle_list_view(m.from_user.id)
    await m.answer(text, reply_markup=kb)


@dp.message(F.text == BTN_DRIVERS)
async def btn_drivers(m: Message):
    text, kb = driver_list_view(m.from_user.id)
    await m.answer(text, reply_markup=kb)


@dp.message(F.text == BTN_TABLE)
async def btn_table(m: Message):
    await m.bot.send_chat_action(m.chat.id, "upload_photo")
    await send_tables(m.chat.id, m.from_user.id)


@dp.message(F.text == BTN_EXPIRING)
async def btn_expiring(m: Message):
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
async def btn_reminders(m: Message):
    rems = dbm.list_reminders(m.from_user.id)
    if not rems:
        await m.answer("Hozircha eslatma yo'q.\nMasalan: «25.11.2026 da O'zbekistonga kirish navbati olish eslat» deb yozing.")
        return
    for r in rems[:20]:
        text = f"🔔 {dbm.reminder_title(r)}\n"
        if r.get("plate"):
            text += f"🚛 {r['plate']}\n"
        text += f"📅 {dbm.fmt_date(r['remind_date'])}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"rd:{r['id']}")]])
        await m.answer(text, reply_markup=kb)


@dp.message(F.text == BTN_EXCEL)
async def btn_excel(m: Message):
    data = await asyncio.to_thread(tables.excel_bytes, m.from_user.id)
    await m.answer_document(BufferedInputFile(data, "logistika_hisobot.xlsx"),
                            caption="📥 Furalar, haydovchilar va eslatmalar (rangli).")


# ====================================================================== INLINE TUGMALAR

@dp.callback_query(F.data == "vl")
async def cb_vehicle_list(cb: CallbackQuery):
    text, kb = vehicle_list_view(cb.from_user.id)
    await safe_edit(cb.message, text, kb)
    await cb.answer()


@dp.callback_query(F.data == "dl")
async def cb_driver_list(cb: CallbackQuery):
    text, kb = driver_list_view(cb.from_user.id)
    await safe_edit(cb.message, text, kb)
    await cb.answer()


@dp.callback_query(F.data.startswith("v:"))
async def cb_vehicle(cb: CallbackQuery):
    uid, vid = cb.from_user.id, int(cb.data[2:])
    v = dbm.get_vehicle(uid, vid)
    if not v:
        await cb.answer("Fura topilmadi", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📎 Hujjat fayllari", callback_data=f"vf:{vid}")],
        [InlineKeyboardButton(text="🔙 Furalar ro'yxati", callback_data="vl")]])
    await safe_edit(cb.message, vehicle_card(uid, v), kb)
    await cb.answer()


@dp.callback_query(F.data.startswith("d:"))
async def cb_driver(cb: CallbackQuery):
    uid, did = cb.from_user.id, int(cb.data[2:])
    d = dbm.get_driver(uid, did)
    if not d:
        await cb.answer("Haydovchi topilmadi", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📎 Hujjat fayllari", callback_data=f"df:{did}")],
        [InlineKeyboardButton(text="🔙 Haydovchilar ro'yxati", callback_data="dl")]])
    await safe_edit(cb.message, driver_card(uid, d), kb)
    await cb.answer()


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


@dp.callback_query(F.data.startswith("vf:"))
async def cb_vehicle_files(cb: CallbackQuery):
    await _send_owner_files(cb, "vehicle", int(cb.data[3:]))


@dp.callback_query(F.data.startswith("df:"))
async def cb_driver_files(cb: CallbackQuery):
    await _send_owner_files(cb, "driver", int(cb.data[3:]))


@dp.callback_query(F.data.startswith("rd:"))
async def cb_reminder_done(cb: CallbackQuery):
    ok = dbm.mark_reminder_done(cb.from_user.id, int(cb.data[3:]))
    if ok:
        await safe_edit(cb.message, (cb.message.text or "") + "\n\n✅ Bajarildi")
    await cb.answer("Bajarildi" if ok else "Topilmadi")


# ====================================================================== HUJJAT (RASM / PDF) QABUL QILISH

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf"}


@dp.message(F.photo | F.document)
async def on_file(m: Message):
    uid = m.from_user.id
    if m.photo:
        file_id, mime, kind = m.photo[-1].file_id, "image/jpeg", "photo"
    else:
        doc = m.document
        mime = (doc.mime_type or "").lower()
        if mime not in ALLOWED_MIME:
            await m.answer("Faqat rasm (JPG, PNG) yoki PDF qabul qilaman. Boshqa formatdagi hujjatni PDF yoki rasm qilib yuboring.")
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
        await wait.edit_text("👤 Bu haydovchi hujjati, lekin ism ko'rinmadi. Rasmga izoh qilib haydovchi ismini "
                             "yozib qayta yuboring.")
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
    if len(PENDING) > 500:
        PENDING.clear()
    PENDING[token] = {"uid": uid, "data": data, "file_id": file_id, "kind": kind}
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Saqlash", callback_data=f"ok:{token}"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"no:{token}")]])
    await wait.edit_text("\n".join(lines), reply_markup=kb)


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
    await safe_edit(cb.message,
                    (cb.message.text or "").replace("\nSaqlaymizmi?", "") +
                    f"\n\n✅ Saqlandi (#{doc_id}). Xato bo'lsa, menga yozing, masalan: «muddatini 06.10.2027 ga o'zgartir».")
    await cb.answer("Saqlandi")


@dp.callback_query(F.data.startswith("no:"))
async def cb_cancel(cb: CallbackQuery):
    PENDING.pop(cb.data[3:], None)
    await safe_edit(cb.message, "❌ Bekor qilindi.")
    await cb.answer()


# ====================================================================== SUHBAT (AI + BAZA)

@dp.message(F.text)
async def on_text(m: Message):
    await m.bot.send_chat_action(m.chat.id, "typing")
    ctx = None
    try:
        answer, ctx = await ai.chat(m.from_user.id, m.text)
    except Exception:
        logging.exception("Suhbat xatosi")
        answer = "Xatolik yuz berdi. Birozdan keyin qayta urinib ko'ring."
    await send_long(m, answer)
    if ctx and ctx.tables:
        await send_tables(m.chat.id, m.from_user.id, tuple(sorted(ctx.tables)))


# ====================================================================== ISHGA TUSHIRISH

async def main():
    if not config.BOT_TOKEN or not config.ANTHROPIC_API_KEY:
        raise SystemExit(".env faylida BOT_TOKEN va ANTHROPIC_API_KEY to'ldirilmagan.")
    dbm.init_db()
    task = asyncio.create_task(reminders.reminder_loop(bot))  # noqa: F841 (havola saqlanadi)
    logging.info("Bot ishga tushdi")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
