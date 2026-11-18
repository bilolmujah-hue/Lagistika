"""Userbot: shaxsiy Telegram akkauntni (Telethon) botga ulash.

- Ulash: telefon -> kod -> (2 bosqichli parol). Hammasi bot.py dagi suhbat orqali, AI (Claude) ga yuborilmaydi.
- Sessiya bazada saqlanadi (SESSION_KEY berilsa shifrlangan).
- Guruhlardan xabar o'qish, lichkaga yozish, kelgan lichka xabarlarini cargo.py ga uzatish.
"""
import asyncio
import logging
import time

from telethon import TelegramClient, errors, events, types
from telethon.sessions import StringSession

import config
import database as dbm

BOT = None            # aiogram Bot (bot.py o'rnatadi) - adminga xabar yuborish uchun
CLIENTS: dict = {}    # admin_id -> TelegramClient
ME: dict = {}         # admin_id -> userbot akkauntining o'z ID si
_LOGIN: dict = {}     # ulanish jarayonidagi vaqtinchalik holat
GROUP_CACHE: dict = {}
LAST_SEND: dict = {}  # (admin_id, odam_id) -> oxirgi yuborilgan vaqt (o'zimiz yozganni "qo'lda yozdi" deb adashmaslik uchun)


# ====================================================================== SESSIYANI SAQLASH

def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(config.SESSION_KEY.encode())


def _enc(s: str) -> str:
    return "enc:" + _fernet().encrypt(s.encode()).decode() if config.SESSION_KEY else s


def _dec(s: str) -> str:
    return _fernet().decrypt(s[4:].encode()).decode() if s.startswith("enc:") else s


def is_connected(uid: int) -> bool:
    c = CLIENTS.get(uid)
    return bool(c and c.is_connected())


# ====================================================================== ULANISH (LOGIN)

async def login_cancel(uid: int):
    st = _LOGIN.pop(uid, None)
    if st:
        try:
            await st["client"].disconnect()
        except Exception:
            pass


async def login_start(uid: int, phone: str):
    """1-qadam: Telegram shu raqamga kod yuboradi (akkauntning o'zidagi Telegram ilovasiga)."""
    if not config.TG_API_ID or not config.TG_API_HASH:
        raise RuntimeError("TG_API_ID va TG_API_HASH .env faylida to'ldirilmagan (my.telegram.org dan oling)")
    await login_cancel(uid)
    c = TelegramClient(StringSession(), config.TG_API_ID, config.TG_API_HASH)
    await c.connect()
    try:
        sent = await c.send_code_request(phone)
    except Exception:
        await c.disconnect()
        raise
    _LOGIN[uid] = {"client": c, "phone": phone, "hash": sent.phone_code_hash}


async def login_code(uid: int, code: str) -> str:
    """2-qadam. 'ok' yoki 'password' (2 bosqichli parol kerak) qaytaradi."""
    st = _LOGIN[uid]
    try:
        await st["client"].sign_in(phone=st["phone"], code=code, phone_code_hash=st["hash"])
    except errors.SessionPasswordNeededError:
        return "password"
    await _finish_login(uid, st)
    return "ok"


async def login_password(uid: int, password: str):
    """3-qadam (kerak bo'lsa): 2 bosqichli tekshiruv paroli."""
    st = _LOGIN[uid]
    await st["client"].sign_in(password=password)
    await _finish_login(uid, st)


async def _finish_login(uid: int, st: dict):
    c = st["client"]
    dbm.ub_save_session(uid, st["phone"], _enc(c.session.save()))
    _LOGIN.pop(uid, None)
    await _activate(uid, c)
    logging.info("Userbot ulandi (admin %s)", uid)


async def _activate(uid: int, c: TelegramClient):
    old = CLIENTS.get(uid)
    if old and old is not c:
        try:
            await old.disconnect()
        except Exception:
            pass
    CLIENTS[uid] = c
    ME[uid] = (await c.get_me()).id
    _attach(uid, c)
    asyncio.create_task(_warm(uid))


async def start_all():
    """Bot ishga tushganda saqlangan sessiyalarni qayta ulaydi."""
    for row in dbm.ub_all_sessions():
        uid = row["user_id"]
        try:
            c = TelegramClient(StringSession(_dec(row["session"])), config.TG_API_ID, config.TG_API_HASH)
            await c.connect()
            if not await c.is_user_authorized():
                dbm.ub_delete_session(uid)
                await c.disconnect()
                if BOT:
                    await BOT.send_message(uid, "⚠️ Userbot sessiyasi tugagan. Qaytadan ulang: «🤖 Yuk qidirish» tugmasi.")
                continue
            await _activate(uid, c)
        except Exception:
            logging.exception("Userbotni ulab bo'lmadi (admin %s)", uid)


async def disconnect(uid: int):
    """Userbotni uzadi va Telegram'dagi sessiyani o'chiradi (log out)."""
    c = CLIENTS.pop(uid, None)
    if c:
        try:
            await c.log_out()
        except Exception:
            try:
                await c.disconnect()
            except Exception:
                pass
    dbm.ub_delete_session(uid)
    ME.pop(uid, None)


def describe_error(e: Exception) -> str:
    if isinstance(e, errors.PhoneNumberInvalidError):
        return "Telefon raqam noto'g'ri. Xalqaro formatda yuboring, masalan +998901234567"
    if isinstance(e, errors.PhoneCodeInvalidError):
        return "Kod noto'g'ri. Qaytadan yuboring."
    if isinstance(e, errors.PhoneCodeExpiredError):
        return "Kod eskirgan. Telefon raqamni qaytadan yuboring."
    if isinstance(e, errors.PasswordHashInvalidError):
        return "Parol noto'g'ri. Qaytadan yuboring."
    if isinstance(e, errors.FloodWaitError):
        return f"Telegram vaqtincha cheklov qo'ydi. {e.seconds // 60 + 1} daqiqadan keyin urinib ko'ring."
    if isinstance(e, errors.ApiIdInvalidError):
        return "TG_API_ID / TG_API_HASH noto'g'ri."
    return f"Xatolik: {e}"


# ====================================================================== KELGAN XABARLAR

def _attach(uid: int, c: TelegramClient):
    @c.on(events.NewMessage(incoming=True))
    async def _incoming(ev):
        if not ev.is_private:
            return
        try:
            import cargo  # circular import bo'lmasligi uchun shu yerda
            await cargo.on_private_message(uid, ev)
        except Exception:
            logging.exception("Kelgan lichka xabarini qayta ishlashda xato")

    @c.on(events.NewMessage(outgoing=True))
    async def _outgoing(ev):
        # Admin o'zi (telefonidan) yozsa - AI to'xtaydi
        if not ev.is_private or time.time() - LAST_SEND.get((uid, ev.chat_id), 0) < 8:
            return
        try:
            import cargo
            await cargo.on_manual_message(uid, ev.chat_id)
        except Exception:
            logging.exception("Qo'lda yozilgan xabarni qayta ishlashda xato")


# ====================================================================== GURUHLAR

async def _warm(uid: int):
    """Qayta ishga tushgandan keyin tanlangan guruhlarni Telethon xotirasiga yuklaydi."""
    ids = {g["chat_id"] for g in dbm.ub_active_groups(uid)}
    if not ids:
        return
    try:
        async for d in CLIENTS[uid].iter_dialogs():
            ids.discard(d.id)
            if not ids:
                break
    except Exception:
        logging.exception("Guruhlarni yuklashda xato")


async def list_groups(uid: int) -> list:
    """Akkauntdagi barcha guruhlar (kanallar emas, chunki kanalda odamlar yozmaydi)."""
    out = []
    async for d in CLIENTS[uid].iter_dialogs():
        if d.is_group:
            out.append({"chat_id": d.id, "title": d.name or str(d.id)})
    GROUP_CACHE[uid] = out
    return out


async def fetch_messages(uid: int, chat_id: int, since, limit: int = 5000) -> list:
    """Guruhdan `since` (UTC datetime) dan keyingi xabarlar. Faqat oddiy foydalanuvchilar yozganlari."""
    out = []
    async for m in CLIENTS[uid].iter_messages(chat_id, limit=limit, wait_time=1):
        if m.date < since:
            break
        text = (m.raw_text or "").strip()
        s = m.sender
        if not text or not isinstance(s, types.User) or s.bot or s.deleted or s.is_self:
            continue
        chat = m.chat
        uname = getattr(chat, "username", None)
        link = (f"https://t.me/{uname}/{m.id}" if uname
                else f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{m.id}")
        out.append({
            "msg_id": m.id, "text": text, "date": m.date, "sender_id": s.id,
            "access_hash": s.access_hash, "username": s.username,
            "name": " ".join(x for x in (s.first_name, s.last_name) if x) or "Noma'lum",
            "link": link,
        })
    return out


# ====================================================================== LICHKAGA YOZISH

async def send_dm(uid: int, user_id: int, access_hash, text: str):
    """Odamga lichkada yozadi ("yozmoqda..." ko'rsatib, odamdek). Telethon xatolari tashqarida ushlanadi."""
    c = CLIENTS[uid]
    peer = types.InputPeerUser(user_id, access_hash or 0) if access_hash else user_id
    LAST_SEND[(uid, user_id)] = time.time()
    try:
        async with c.action(peer, "typing"):
            await asyncio.sleep(min(7.0, 1.0 + len(text) / 30))
        msg = await c.send_message(peer, text)
    finally:
        LAST_SEND[(uid, user_id)] = time.time()
    return msg
