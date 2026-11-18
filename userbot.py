"""Telegram userbot akkauntlari (Telethon). Uch xil akkaunt bor:

  pool - admin qo'shadigan "yuk qidirish" akkauntlari (masalan 10 ta). Guruhlardan e'lon o'qiydi va
         barcha foydalanuvchilar nomidan yuk egalari bilan lichkada AI orqali gaplashadi.
  pay  - to'lov akkaunti. @CardXabarBot shu akkauntga kartaga tushgan pullarni xabar qiladi.
  user - foydalanuvchining shaxsiy akkaunti ("Avto xabar" uchun guruhlarga e'lon yuboradi).

Sessiyalar bazada (SESSION_KEY bo'lsa shifrlangan) saqlanadi.
"""
import asyncio
import logging
import time

from telethon import TelegramClient, errors, events, types
from telethon.sessions import StringSession

import config
import core
import database as dbm

CLIENTS: dict = {}       # account_id -> TelegramClient
ME: dict = {}            # account_id -> akkauntning o'z Telegram ID si
_LOGIN: dict = {}        # ulanish jarayoni: kim ulayapti (bot user_id) -> {...}
GROUP_CACHE: dict = {}   # account_id -> [{"chat_id","title"}]
LAST_SEND: dict = {}     # (account_id, peer_id) -> vaqt (o'zimiz yozganini "qo'lda" deb adashmaslik uchun)
_ACC_LOCKS: dict = {}    # account_id -> Lock (bir akkauntdan yangi odamlarga navbat bilan yozish)


def is_connected(aid) -> bool:
    c = CLIENTS.get(aid)
    return bool(c and c.is_connected())


def get(aid):
    return CLIENTS.get(aid)


def acc_lock(aid) -> asyncio.Lock:
    return _ACC_LOCKS.setdefault(aid, asyncio.Lock())


def connected_pool() -> list:
    return [a for a in dbm.acc_list("pool", active_only=True) if is_connected(a["id"])]


def pay_client():
    a = dbm.pay_account()
    return (a, CLIENTS.get(a["id"])) if a and is_connected(a["id"]) else (a, None)


def user_client(uid):
    a = dbm.user_account(uid)
    return (a, CLIENTS.get(a["id"])) if a and is_connected(a["id"]) else (a, None)


# ====================================================================== ULANISH (LOGIN)

async def login_cancel(uid: int):
    st = _LOGIN.pop(uid, None)
    if st:
        try:
            await st["client"].disconnect()
        except Exception:
            pass


async def login_start(uid: int, phone: str):
    """1-qadam: Telegram shu raqamga kod yuboradi."""
    if not config.TG_API_ID or not config.TG_API_HASH:
        raise RuntimeError("TG_API_ID va TG_API_HASH sozlanmagan (my.telegram.org dan oling)")
    await login_cancel(uid)
    c = TelegramClient(StringSession(), config.TG_API_ID, config.TG_API_HASH,
                       device_model="Logistika Bot", system_version="1.0", app_version="1.0")
    await c.connect()
    try:
        sent = await c.send_code_request(phone)
    except Exception:
        await c.disconnect()
        raise
    _LOGIN[uid] = {"client": c, "phone": phone, "hash": sent.phone_code_hash}


async def login_code(uid: int, code: str) -> str:
    """2-qadam. 'ok' yoki 'password' qaytaradi."""
    st = _LOGIN[uid]
    try:
        await st["client"].sign_in(phone=st["phone"], code=code, phone_code_hash=st["hash"])
    except errors.SessionPasswordNeededError:
        return "password"
    return "ok"


async def login_password(uid: int, password: str):
    st = _LOGIN[uid]
    await st["client"].sign_in(password=password)


async def login_finish(uid: int, kind: str) -> int:
    """Ulanishni yakunlaydi, akkauntni bazaga yozadi va ishga tushiradi. account_id qaytaradi."""
    st = _LOGIN.pop(uid)
    c = st["client"]
    me = await c.get_me()
    # Shu Telegram akkaunt allaqachon boshqa joyda ulangan bo'lsa - eskisini olib tashlaymiz
    for a in dbm.acc_list():
        if a["tg_id"] == me.id and a["kind"] == kind:
            await remove(a["id"], logout=False)
    if kind in ("user", "pay"):
        for a in (dbm.acc_list("user", uid) if kind == "user" else dbm.acc_list("pay")):
            await remove(a["id"], logout=False)
    name = " ".join(x for x in (me.first_name, me.last_name) if x) or str(me.id)
    aid = dbm.acc_add(kind, uid, st["phone"], core.enc(c.session.save()), me.id, me.username, name)
    await _activate(aid, kind, c)
    logging.info("Akkaunt ulandi: #%s %s (%s)", aid, kind, name)
    return aid


async def _activate(aid: int, kind: str, c: TelegramClient):
    old = CLIENTS.get(aid)
    if old and old is not c:
        try:
            await old.disconnect()
        except Exception:
            pass
    CLIENTS[aid] = c
    try:
        ME[aid] = (await c.get_me()).id
    except Exception:
        pass
    _attach(aid, kind, c)
    dbm.acc_update(aid, status="ok")
    asyncio.create_task(_warm(aid))


async def start_all():
    """Bot ishga tushganda saqlangan barcha akkauntlarni qayta ulaydi."""
    if not config.TG_API_ID or not config.TG_API_HASH:
        logging.warning("TG_API_ID/TG_API_HASH yo'q - userbotlar ishga tushirilmadi")
        return
    for row in dbm.acc_list():
        if not row["active"]:
            continue
        await connect_row(row)


async def connect_row(row) -> bool:
    aid = row["id"]
    try:
        c = TelegramClient(StringSession(core.dec(row["session"])), config.TG_API_ID, config.TG_API_HASH,
                           device_model="Logistika Bot", system_version="1.0", app_version="1.0")
        await c.connect()
        if not await c.is_user_authorized():
            await c.disconnect()
            dbm.acc_update(aid, status="expired", active=0)
            msg = f"⚠️ Userbot sessiyasi tugagan: {row['name'] or row['phone']} ({row['kind']}). Qaytadan ulang."
            if row["kind"] == "user":
                await core.send(row["owner_id"], "⚠️ «Avto xabar» uchun ulangan akkauntingiz sessiyasi tugadi. "
                                                 "«📢 Avto xabar» bo'limidan qaytadan ulang.")
            else:
                await core.notify_admins(msg)
            return False
        await _activate(aid, row["kind"], c)
        return True
    except Exception as e:
        logging.exception("Akkauntni ulab bo'lmadi (#%s)", aid)
        dbm.acc_update(aid, status=f"xato: {e}"[:200])
        return False


async def remove(aid: int, logout: bool = True):
    """Akkauntni uzadi, (xohlansa) Telegram'dagi sessiyani o'chiradi va bazadan olib tashlaydi."""
    c = CLIENTS.pop(aid, None)
    if c:
        try:
            if logout:
                await c.log_out()
            else:
                await c.disconnect()
        except Exception:
            try:
                await c.disconnect()
            except Exception:
                pass
    ME.pop(aid, None)
    GROUP_CACHE.pop(aid, None)
    dbm.acc_delete(aid)


async def pause(aid: int):
    c = CLIENTS.pop(aid, None)
    if c:
        try:
            await c.disconnect()
        except Exception:
            pass
    dbm.acc_update(aid, active=0)


async def resume(aid: int) -> bool:
    dbm.acc_update(aid, active=1)
    row = dbm.acc_get(aid)
    return await connect_row(row) if row else False


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
    if isinstance(e, KeyError):
        return "Ulanish jarayoni topilmadi. Boshidan boshlang."
    return f"Xatolik: {e}"


# ====================================================================== KELGAN XABARLAR

def _attach(aid: int, kind: str, c: TelegramClient):
    if kind == "pool":
        @c.on(events.NewMessage(incoming=True))
        async def _incoming(ev):
            if not ev.is_private:
                return
            try:
                import cargo
                await cargo.on_private_message(aid, ev)
            except Exception:
                logging.exception("Kelgan lichka xabarini qayta ishlashda xato")

        @c.on(events.NewMessage(outgoing=True))
        async def _outgoing(ev):
            if not ev.is_private or time.time() - LAST_SEND.get((aid, ev.chat_id), 0) < 8:
                return
            try:
                import cargo
                await cargo.on_manual_message(aid, ev.chat_id)
            except Exception:
                logging.exception("Qo'lda yozilgan xabarni qayta ishlashda xato")

    elif kind == "pay":
        @c.on(events.NewMessage(incoming=True))
        async def _card(ev):
            if not ev.is_private:
                return
            try:
                s = await ev.get_sender()
                if (getattr(s, "username", "") or "").lower() != config.CARD_BOT:
                    return
                import payments
                await payments.on_card_message(ev.raw_text or "", ev.id, ev.date, aid)
            except Exception:
                logging.exception("CardXabar xabarini qayta ishlashda xato")


# ====================================================================== GURUHLAR

async def _warm(aid: int):
    """Qayta ishga tushgandan keyin dialoglarni Telethon xotirasiga yuklaydi (guruhga yozish uchun kerak)."""
    try:
        await CLIENTS[aid].get_dialogs(limit=500)
    except Exception:
        logging.warning("Dialoglarni yuklab bo'lmadi (#%s)", aid, exc_info=True)


async def list_groups(aid: int) -> list:
    """Akkauntdagi barcha guruhlar (kanallar emas)."""
    out = []
    async for d in CLIENTS[aid].iter_dialogs():
        if d.is_group:
            out.append({"chat_id": d.id, "title": d.name or str(d.id)})
    GROUP_CACHE[aid] = out
    return out


async def fetch_messages(aid: int, chat_id: int, since, limit: int = 3000) -> list:
    """Guruhdan `since` (UTC) dan keyingi oddiy foydalanuvchilar yozgan xabarlar."""
    out = []
    async for m in CLIENTS[aid].iter_messages(chat_id, limit=limit, wait_time=1):
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
            "link": link, "chat_title": getattr(chat, "title", "") or "",
        })
    return out


# ====================================================================== LICHKAGA YOZISH

async def send_dm(aid: int, user_id: int, access_hash, text: str, username=None):
    """Odamga lichkada yozadi ("yozmoqda..." ko'rsatib). Telethon xatolari chaqiruvchida ushlanadi."""
    c = CLIENTS[aid]
    if access_hash:
        peer = types.InputPeerUser(user_id, access_hash)
    elif username:
        peer = await c.get_input_entity(username)
    else:
        peer = user_id
    LAST_SEND[(aid, user_id)] = time.time()
    try:
        async with c.action(peer, "typing"):
            await asyncio.sleep(min(7.0, 1.0 + len(text) / 30))
        msg = await c.send_message(peer, text)
    finally:
        LAST_SEND[(aid, user_id)] = time.time()
    return msg
