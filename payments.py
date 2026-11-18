"""TO'LOV TIZIMI (avtomatik).

Qanday ishlaydi:
 1. Foydalanuvchi «To'lov qilish» -> «Tushundim» bosadi. Bot UNIKAL summa beradi: narx + 1..999 so'm
    (masalan 100 347 so'm). Aynan shu oxirgi raqamlar orqali kimning puli kelganini aniqlaymiz,
    chunki @CardXabarBot xabarida to'lovchining ismi bo'lmaydi.
 2. Bot karta raqami, egasi, summa va 10:00 dan 00:00 gacha sanaladigan taymer ko'rsatadi.
 3. "pay" userbot akkauntiga @CardXabarBot dan "➕ 100 347.00 UZS" kabi tushum xabari keladi ->
    parse_card_message() o'qiydi -> shu summali ochiq to'lov topilsa obuna DARHOL ochiladi
    (chek yuborilmasa ham).
 4. Foydalanuvchi chek rasmini yuboradi -> AI chekdan summa/sana/kartani o'qiydi -> kartaga haqiqatan
    tushgan pul (card_txns) bilan solishtiriladi. Soxta chek o'tmaydi: obuna faqat HAQIQIY tushum
    topilganda ochiladi.
 5. Tushum topilmasa (summa xato o'tkazilgan, CardXabar kechikdi va h.k.) - chek adminga
    «✅ Tasdiqlash / ❌ Rad etish» tugmalari bilan boradi.
 Har bir tushum faqat BITTA to'lovga bog'lanadi (bir chek bilan ikki marta ochib bo'lmaydi).
"""
import asyncio
import io
import json
import logging
import random
import re
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup as K
from aiogram.types import Message

import config
import core
import database as dbm
import userbot

CHECK_MIN = 3            # chek kelgach karta tushumini kamida shuncha daqiqa kutamiz
LATE_MATCH_HOURS = 3     # vaqti tugagan to'lovga ham shuncha soat ichida kelgan tushum bog'lanadi
_TIMERS: dict = {}
_LOCK = asyncio.Lock()

# ====================================================================== CardXabar XABARINI O'QISH

_AMT_RE = re.compile(r"([0-9][0-9   ]*(?:[.,][0-9]{1,2})?)\s*(?:UZS|USD|RUB|сум|so'?m|sum)\b", re.I)
_BAL_RE = re.compile(r"💵|balans|баланс|ostatok|остаток|qoldiq", re.I)
_CREDIT_WORDS = re.compile(r"perevod na kartu|popolnenie|пополнение|перевод на карту|zachislenie|зачисление|"
                           r"kirim|tushum|o'tkazma keldi|поступление|postuplenie", re.I)


def _num(s) -> float:
    return float(re.sub(r"[   ]", "", s).replace(",", "."))


def parse_card_message(text: str):
    """@CardXabarBot xabari -> {is_credit, amount, card, merchant, title, time, balance} yoki None.

    Namuna:
        🟢 Perevod na kartu
        ➕ 14 550.00 UZS
        💳 ***5894
        📍 CASH TO CARD 3 PAYNET, UZ
        🕓 18.11.26 19:39
        💵 649 523.68 UZS
    """
    if not text or not _AMT_RE.search(text):
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    first = lines[0]
    title = re.sub(r"^[^0-9A-Za-zА-Яа-яЁё]+", "", first).strip()
    plus = "➕" in text or re.search(r"(^|\n)\s*\+\s*\d", text)
    minus = "➖" in text or re.search(r"(^|\n)\s*[-−]\s*\d", text)
    green, red = first.startswith("🟢"), first.startswith("🔴")
    is_credit = bool((plus or green or _CREDIT_WORDS.search(text)) and not (minus or red))

    amount = None
    for ln in lines:
        if "➕" in ln or "➖" in ln or re.match(r"^[+\-−]\s*\d", ln):
            m = _AMT_RE.search(ln)
            if m:
                amount = _num(m.group(1))
                break
    if amount is None:
        for ln in lines:
            if _BAL_RE.search(ln):
                continue
            m = _AMT_RE.search(ln)
            if m:
                amount = _num(m.group(1))
                break
    if amount is None:
        return None

    balance = None
    for ln in reversed(lines):
        if _BAL_RE.search(ln):
            m = _AMT_RE.search(ln)
            if m:
                balance = _num(m.group(1))
                break
    card = re.search(r"\*+\s?(\d{4})", text)
    t = re.search(r"(\d{2})\.(\d{2})\.(\d{2,4})\s+(\d{2}):(\d{2})", text)
    when = None
    if t:
        d, mo, y, h, mi = t.groups()
        y = int(y) + (2000 if len(y) == 2 else 0)
        try:
            when = datetime(y, int(mo), int(d), int(h), int(mi), tzinfo=config.TZ).isoformat(timespec="seconds")
        except ValueError:
            when = None
    merchant = next((re.sub(r"^📍\s*", "", ln) for ln in lines if ln.startswith("📍")), None)
    return {"is_credit": is_credit, "amount": amount, "card": card.group(1) if card else None,
            "merchant": merchant, "title": title, "time": when, "balance": balance}


# ====================================================================== SOZLAMALAR

def card_digits():
    return dbm.digits(dbm.setting("card_number"))


def card4():
    c = card_digits()
    return c[-4:] if len(c) >= 4 else None


def auto_enabled() -> bool:
    _, c = userbot.pay_client()
    return dbm.setting("auto_pay") == "1" and c is not None


def configured() -> bool:
    s = dbm.settings()
    return len(dbm.digits(s["card_number"])) >= 16 and bool((s["card_holder"] or "").strip())


def price_info():
    return dbm.setting_int("price", 100000), dbm.setting_int("period_days", 30), dbm.setting_int("pay_window_min", 10)


def alloc_amount(price: int) -> int:
    used = dbm.amounts_in_use()
    for _ in range(300):
        a = price + random.randint(1, 999)
        if a not in used:
            return a
    return price + random.randint(1000, 9999)


def explain_text() -> str:
    price, days, win = price_info()
    return (
        "💳 Obuna to'lovi qanday ishlaydi?\n\n"
        "1️⃣ «✅ Tushundim» tugmasini bosing - bot karta raqami, summa va "
        f"{win} daqiqalik taymer beradi.\n"
        "2️⃣ Click, Payme, Uzum yoki bank ilovangiz orqali kartaga AYNAN ko'rsatilgan summani o'tkazing. "
        "Summaning oxirgi raqamlari muhim - to'lovingiz aynan shu orqali avtomatik aniqlanadi.\n"
        "3️⃣ To'lov chekini (skrinshot) shu chatga yuboring.\n"
        "4️⃣ Pul kartaga tushishi bilan bot avtomatik tekshiradi va obunangizni ochadi ✅\n\n"
        f"💰 Narx: {core.money(price)} so'm (+ aniqlash uchun bir necha so'm)\n"
        f"📅 Muddat: {days} kun\n\n"
        "Taymer tugaguncha to'lov qilishga ulgurmasangiz - qaytadan boshlashingiz mumkin.")


def explain_kb():
    return K(inline_keyboard=[[B(text="✅ Tushundim", callback_data="pay:go")],
                              [B(text="❌ Keyinroq", callback_data="pay:later")]])


# ====================================================================== TO'LOV OYNASI

def _remaining(p) -> int:
    exp = dbm._dt(p["expires_at"])
    return max(0, int((exp - datetime.now(config.TZ)).total_seconds())) if exp else 0


def render(p):
    s = dbm.settings()
    card = card_digits()
    card_fmt = " ".join(card[i:i + 4] for i in range(0, len(card), 4))
    rem = _remaining(p)
    mm, ss = divmod(rem, 60)
    lines = [
        "💳 <b>Obuna to'lovi</b>\n",
        "1️⃣ Quyidagi kartaga <b>AYNAN</b> shu summani o'tkazing:",
        f"💳 Karta: <code>{card}</code>\n      ({core.esc(card_fmt)})",
        f"👤 Egasi: <b>{core.esc(s['card_holder'])}</b>",
        f"💰 Summa: <code>{p['amount']}</code> so'm  (<b>{core.money(p['amount'])}</b>)",
        f"📅 Tarif: {p['days']} kun\n",
        "2️⃣ To'lov chekini (skrinshot) shu chatga yuboring.\n",
    ]
    if p["status"] == "checking":
        lines.append(f"🔍 Chek qabul qilindi, karta tushumi tekshirilmoqda... <b>{mm:02d}:{ss:02d}</b>")
    else:
        lines.append(f"⏳ Qolgan vaqt: <b>{mm:02d}:{ss:02d}</b>")
    lines.append("\n⚠️ Summani yaxlitlamang - to'lovingiz aynan shu summa orqali avtomatik aniqlanadi.\n"
                 "💡 Karta raqami va summani bosib nusxa olishingiz mumkin.")
    kb = K(inline_keyboard=[[B(text="❌ Bekor qilish", callback_data=f"pay:cancel:{p['id']}")]])
    return "\n".join(lines), kb


async def _edit(p, text, kb=None, html_mode=True):
    if not p.get("chat_id") or not p.get("msg_id"):
        return
    try:
        await core.bot.edit_message_text(text=text, chat_id=p["chat_id"], message_id=p["msg_id"],
                                         reply_markup=kb, parse_mode="HTML" if html_mode else None)
    except Exception as e:
        if "not modified" not in str(e):
            logging.debug("To'lov oynasini yangilab bo'lmadi: %s", e)


async def _send_panel(p, chat_id):
    text, kb = render(p)
    msg = await core.bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
    dbm.update_payment(p["id"], chat_id=chat_id, msg_id=msg.message_id)


async def start(uid: int, chat_id: int):
    """To'lov oynasini ochadi. Xato bo'lsa matn qaytaradi, aks holda None."""
    if not configured():
        await core.notify_admins("⚠️ Foydalanuvchi to'lov qilmoqchi, lekin karta sozlanmagan!\n"
                                 "🛠 Admin panel → 💳 Avto to'lov → karta raqami va egasini kiriting.")
        return "To'lov tizimi hozircha sozlanmagan. Admin xabardor qilindi, birozdan keyin urinib ko'ring."
    p = dbm.open_payment(uid)
    if p and p["status"] in ("pending", "checking") and _remaining(p) > 0:
        old = dict(p)
        await _send_panel(p, chat_id)
        await _edit(old, "♻️ To'lov oynasi pastda qayta ochildi.", None)
        _start_timer(p["id"])
        return None
    if p and p["status"] == "review":
        return "📨 Oldingi to'lovingiz cheki admin tekshiruvida. Tez orada javob beriladi."
    price, days, win = price_info()
    amount = alloc_amount(price)
    exp = (datetime.now(config.TZ) + timedelta(minutes=win)).isoformat(timespec="seconds")
    pid = dbm.create_payment(uid, amount, days, exp, chat_id)
    await _send_panel(dbm.get_payment(pid), chat_id)
    _start_timer(pid)
    return None


def _start_timer(pid):
    t = _TIMERS.get(pid)
    if t and not t.done():
        return
    _TIMERS[pid] = asyncio.create_task(_timer(pid))


async def _timer(pid):
    last = None
    try:
        while True:
            p = dbm.get_payment(pid)
            if not p or p["status"] not in ("pending", "checking"):
                return
            rem = _remaining(p)
            if rem <= 0:
                await _on_timeout(p)
                return
            text, kb = render(p)
            if text != last:
                await _edit(p, text, kb)
                last = text
            await asyncio.sleep(5 if rem > 30 else 2)
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.exception("To'lov taymerida xato (#%s)", pid)
    finally:
        if _TIMERS.get(pid) is asyncio.current_task():
            _TIMERS.pop(pid, None)


async def _on_timeout(p):
    if p["status"] == "checking":
        await to_review(p, "Chek yuborildi, lekin kartaga mos tushum topilmadi")
        return
    dbm.update_payment(p["id"], status="expired")
    await _edit(p, "⌛ To'lov vaqti tugadi.\n\nAgar pul o'tkazgan bo'lsangiz - chek rasmini shu yerga yuboring, "
                   "tekshirib beramiz. Aks holda qaytadan boshlang.",
                K(inline_keyboard=[[B(text="🔄 Qaytadan boshlash", callback_data="pay:go")]]), html_mode=False)


async def cancel(uid, pid) -> bool:
    p = dbm.get_payment(pid)
    if not p or p["user_id"] != uid or p["status"] not in ("pending", "checking", "expired"):
        return False
    dbm.update_payment(pid, status="cancelled")
    t = _TIMERS.pop(pid, None)
    if t:
        t.cancel()
    await _edit(p, "❌ To'lov bekor qilindi.", None, html_mode=False)
    return True


# ====================================================================== FAOLLASHTIRISH

async def activate(pid, tid, method, by=None) -> bool:
    if tid and not dbm.claim_txn(tid, pid):
        return False
    if not dbm.mark_paid(pid, tid, method, by):
        if tid:
            dbm.release_txn(tid)
        return False
    p = dbm.get_payment(pid)
    t = _TIMERS.pop(pid, None)
    if t and t is not asyncio.current_task():
        t.cancel()
    until = dbm.extend_sub(p["user_id"], p["days"] or dbm.setting_int("period_days", 30))
    await _edit(p, f"✅ To'lov qabul qilindi: {core.money(p['amount'])} so'm", None, html_mode=False)
    await core.send(p["user_id"], f"🎉 To'lov tasdiqlandi!\n\n✅ Obunangiz {dbm.fmt_dt(until)} gacha faol.\n"
                                  "Barcha imkoniyatlardan foydalanishingiz mumkin. Rahmat! 🤝")
    u = dbm.get_user(p["user_id"]) or {}
    how = {"auto": "avtomatik (CardXabar)", "auto_receipt": "chek + CardXabar", "manual": f"admin tasdiqladi ({by})",
           "admin": "admin"}.get(method, method)
    await core.notify_admins(
        f"💰 Yangi to'lov: {core.money(p['amount'])} so'm\n👤 {u.get('name') or '-'} "
        f"{('@' + u['username']) if u.get('username') else ''} (ID {p['user_id']})\n📞 {u.get('phone') or '-'}\n"
        f"✅ Usul: {how}\n📅 Obuna: {dbm.fmt_dt(until)} gacha")
    return True


async def match_txn(tid):
    """Yangi tushumni ochiq to'lovlardan biriga bog'laydi."""
    async with _LOCK:
        t = dbm.get_txn(tid)
        if not t or t["payment_id"] or not t["is_credit"]:
            return False
        amt = float(t["amount"] or 0)
        when = dbm._dt(t["txn_time"]) or dbm._dt(t["received_at"]) or datetime.now(config.TZ)
        since = (datetime.now(config.TZ) - timedelta(hours=LATE_MATCH_HOURS)).isoformat(timespec="seconds")
        if abs(amt - round(amt)) < 0.01:
            for p in dbm.payments_matching_amount(int(round(amt)), since):
                created = dbm._dt(p["created"])
                if created and created - timedelta(minutes=3) <= when:
                    return await activate(p["id"], tid, "auto")
        # Summa xato o'tkazilgan, lekin chekdagi summa shu tushumga teng bo'lsa
        for p in dbm.payments_by_status("pending", "checking", "review", limit=200):
            if p["created"] < since:
                continue
            ra = (json.loads(p["receipt_info"] or "{}") or {}).get("amount")
            try:
                if ra and abs(float(ra) - amt) < 0.01 and dbm._dt(p["created"]) - timedelta(minutes=3) <= when:
                    return await activate(p["id"], tid, "auto_receipt")
            except (TypeError, ValueError):
                continue
    return False


async def on_card_message(text: str, msg_id: int, date=None, acc_id=None):
    info = parse_card_message(text)
    if not info:
        return
    if acc_id is None:
        a = dbm.pay_account()
        acc_id = a["id"] if a else 0
    key = int(acc_id) * 10 ** 10 + int(msg_id)
    received = date.astimezone(config.TZ).isoformat(timespec="seconds") if date else None
    tid = dbm.add_card_txn(key, info, text, received)
    if not tid or not info["is_credit"]:
        return
    c4 = card4()
    if c4 and info.get("card") and info["card"] != c4:
        return  # boshqa kartaga tushum
    if dbm.setting("auto_pay") != "1":
        return
    await match_txn(tid)


async def check_card_bot() -> bool:
    _, c = userbot.pay_client()
    if not c:
        return False
    try:
        ent = await c.get_entity(config.CARD_BOT)
        msgs = await c.get_messages(ent, limit=1)
        return bool(msgs)
    except Exception:
        return False


async def catch_up():
    """Bot o'chiq turgan paytda kelgan CardXabar xabarlarini ham o'qib oladi."""
    a, c = userbot.pay_client()
    if not c:
        return
    try:
        ent = await c.get_entity(config.CARD_BOT)
        msgs = await c.get_messages(ent, limit=30)
        for m in reversed(msgs):
            if m.out:
                continue
            await on_card_message(m.raw_text or "", m.id, m.date, a["id"])
    except Exception as e:
        logging.warning("CardXabar tarixini o'qib bo'lmadi: %s", e)


# ====================================================================== CHEK

async def handle_receipt(m: Message, p: dict):
    pid = p["id"]
    if m.photo:
        file_id, mime, kind = m.photo[-1].file_id, "image/jpeg", "photo"
    else:
        mime = (m.document.mime_type or "").lower()
        if mime not in ("image/jpeg", "image/png", "image/webp", "application/pdf"):
            await m.answer("Chekni rasm (skrinshot) yoki PDF ko'rinishida yuboring.")
            return
        file_id, kind = m.document.file_id, "document"
    if p["status"] == "review":
        await m.answer("📨 Chekingiz allaqachon admin tekshiruvida. Tez orada javob beriladi.")
        return
    wait = await m.answer("🔍 Chek tekshirilmoqda...")
    info = {}
    try:
        import ai
        buf = io.BytesIO()
        await core.bot.download(file_id, destination=buf)
        info = await ai.extract_receipt(buf.getvalue(), mime)
    except Exception:
        logging.exception("Chekni o'qishda xato")
    dbm.update_payment(pid, receipt_file_id=file_id, receipt_kind=kind,
                       receipt_info=json.dumps(info, ensure_ascii=False))
    if info.get("is_receipt") is False:
        await core.safe_edit(wait, "🤔 Bu rasm to'lov chekiga o'xshamaydi. To'lov ilovasidagi chek (kvitansiya) "
                                   "skrinshotini yuboring.")
        return
    price = dbm.setting_int("price", 100000)
    c4 = card4()
    async with _LOCK:
        p = dbm.get_payment(pid)
        if p["status"] == "paid":
            await core.safe_edit(wait, "✅ Bu to'lov allaqachon tasdiqlangan.")
            return
        since = (dbm._dt(p["created"]) - timedelta(minutes=3)).isoformat(timespec="seconds")
        cands = dbm.free_credits(p["amount"], since, c4)
        ra = None
        try:
            ra = float(info.get("amount")) if info.get("amount") is not None else None
        except (TypeError, ValueError):
            ra = None
        if not cands and ra and ra >= price * 0.98 and abs(ra - p["amount"]) >= 0.01:
            others = [x for x in dbm.payments_matching_amount(int(round(ra)), since) if x["id"] != pid]
            if not others:
                cands = dbm.free_credits(ra, since, c4)
        if cands:
            ok = await activate(pid, cands[0]["id"], "auto_receipt")
            if ok:
                await wait.delete()
                return
    if not auto_enabled():
        await core.safe_edit(wait, "📨 Chekingiz adminga yuborildi. Tasdiqlangach obunangiz avtomatik ochiladi.")
        await to_review(dbm.get_payment(pid), "Avtomatik tekshiruv o'chiq (to'lov akkaunti ulanmagan)")
        return
    new_exp = max(dbm._dt(p["expires_at"]), datetime.now(config.TZ) + timedelta(minutes=CHECK_MIN))
    dbm.update_payment(pid, status="checking", expires_at=new_exp.isoformat(timespec="seconds"))
    await core.safe_edit(wait, "✅ Chek qabul qilindi. Karta tushumi kutilmoqda - pul kelishi bilan obunangiz "
                               "avtomatik ochiladi (odatda 1-2 daqiqa).")
    _start_timer(pid)


async def to_review(p, reason=""):
    pid = p["id"]
    dbm.update_payment(pid, status="review", note=reason)
    await _edit(p, "📨 Chekingiz adminga tekshiruvga yuborildi. Tasdiqlangach obunangiz avtomatik ochiladi.",
                None, html_mode=False)
    if p["chat_id"] and not p["msg_id"]:
        await core.send(p["user_id"], "📨 Chekingiz adminga tekshiruvga yuborildi.")
    u = dbm.get_user(p["user_id"]) or {}
    info = json.loads(p.get("receipt_info") or "{}") or {}
    cap = (f"🧾 To'lov tekshiruvi #{pid}\n👤 {u.get('name') or '-'} "
           f"{('@' + u['username']) if u.get('username') else ''} (ID {p['user_id']})\n📞 {u.get('phone') or '-'}\n"
           f"💰 Kutilgan summa: {core.money(p['amount'])} so'm\n"
           f"🧾 Chekda: {core.money(info['amount']) if info.get('amount') else '?'} so'm, "
           f"{info.get('date') or ''} {info.get('time') or ''}, karta *{info.get('receiver_card_last4') or '?'}, "
           f"holat: {info.get('status') or '?'}\n"
           f"❗ Sabab: {reason}")
    kb = K(inline_keyboard=[[B(text="✅ Tasdiqlash", callback_data=f"pa:{pid}"),
                             B(text="❌ Rad etish", callback_data=f"pr:{pid}")]])
    for aid in dbm.admin_ids():
        try:
            if p.get("receipt_file_id"):
                if p.get("receipt_kind") == "photo":
                    await core.bot.send_photo(aid, p["receipt_file_id"], caption=cap[:1000], reply_markup=kb)
                else:
                    await core.bot.send_document(aid, p["receipt_file_id"], caption=cap[:1000], reply_markup=kb)
            else:
                await core.bot.send_message(aid, cap, reply_markup=kb)
        except Exception:
            logging.exception("Adminga chek yuborilmadi")


async def reject(pid, by) -> bool:
    p = dbm.get_payment(pid)
    if not p or p["status"] not in ("review", "checking", "expired", "pending"):
        return False
    dbm.update_payment(pid, status="rejected", reviewed_by=by)
    await core.send(p["user_id"], "❌ To'lovingiz tasdiqlanmadi. Chek yoki summa mos kelmadi.\n"
                                  "Savol bo'lsa admin bilan bog'laning yoki qaytadan to'lov qiling.",
                    K(inline_keyboard=[[B(text="💳 Qaytadan to'lash", callback_data="pay:info")]]))
    return True


# ====================================================================== FON SIKLI

async def loop():
    for p in dbm.payments_by_status("pending", "checking", limit=500):
        _start_timer(p["id"])
    n = 0
    while True:
        try:
            if n % 3 == 0:
                await catch_up()
            for p in dbm.payments_by_status("pending", "checking", limit=500):
                t = _TIMERS.get(p["id"])
                if not t or t.done():
                    _start_timer(p["id"])
        except Exception:
            logging.exception("To'lov sikli xatosi")
        n += 1
        await asyncio.sleep(30)
