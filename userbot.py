"""Telegram userbot akkauntlari (Telethon). Uch xil akkaunt bor:

  pool - admin qo'shadigan "yuk qidirish" akkauntlari (masalan 10 ta). Qidiruvda HAMMASI BIR VAQTDA
         ishlaydi: har biri a'zo bo'lgan barcha guruhlardan Telegram serverining global qidiruvi orqali
         e'lon topadi (bitta so'rov = 30-50 ta guruh).
  pay  - to'lov akkaunti. @CardXabarBot shu akkauntga kartaga tushgan pullarni xabar qiladi.
  user - foydalanuvchining shaxsiy akkaunti. BIR MARTA ulanadi va ikki ish uchun ishlatiladi:
         (1) "Avto xabar" - guruhlarga e'lon; (2) topilgan mijozlar bilan lichkada AI suhbat.

Sessiyalar bazada (SESSION_KEY bo'lsa shifrlangan) saqlanadi. Uzilgan akkauntlar watchdog_loop()
orqali avtomatik qayta ulanadi.
"""
import asyncio
import hashlib
import logging
import random
import re
import time

from telethon import TelegramClient, errors, events, functions, types, utils
from telethon.sessions import StringSession

import config
import core
import database as dbm

CLIENTS: dict = {}       # account_id -> TelegramClient
ME: dict = {}            # account_id -> akkauntning o'z Telegram ID si
_LOGIN: dict = {}        # ulanish jarayoni: kim ulayapti (bot user_id) -> {...}
GROUP_CACHE: dict = {}   # account_id -> [{"chat_id","title","can_write","why"}]  (faqat guruhlar)
SEARCH_CHATS: dict = {}  # account_id -> [{"chat_id","title"}]  (guruhlar + kanallar - yuk qidiruvi uchun)
LAST_SEND: dict = {}     # (account_id, peer_id) -> vaqt (o'zimiz yozganini "qo'lda" deb adashmaslik uchun)
_ACC_LOCKS: dict = {}    # account_id -> Lock (bir akkauntdan yangi odamlarga navbat bilan yozish)
SEARCH_COOLDOWN: dict = {}  # account_id -> unix vaqt (FloodWait olgan akkaunt qidiruvdan vaqtincha chetlanadi)
_WD_NOTIFIED: dict = {}


def _new_client(session: str = "") -> TelegramClient:
    return TelegramClient(StringSession(session), config.TG_API_ID, config.TG_API_HASH,
                          device_model="Logistika Bot", system_version="1.0", app_version="1.0",
                          flood_sleep_threshold=15, connection_retries=10, retry_delay=2, auto_reconnect=True)


def is_connected(aid) -> bool:
    c = CLIENTS.get(aid)
    return bool(c and c.is_connected())


def get(aid):
    return CLIENTS.get(aid)


def acc_lock(aid) -> asyncio.Lock:
    return _ACC_LOCKS.setdefault(aid, asyncio.Lock())


def connected_pool() -> list:
    return [a for a in dbm.acc_list("pool", active_only=True) if is_connected(a["id"])]


def search_pool() -> list:
    """Qidiruvga tayyor pool akkauntlar (ulangan va FloodWait da emas)."""
    now = time.time()
    return [a for a in connected_pool() if SEARCH_COOLDOWN.get(a["id"], 0) < now]


def groups_total(accounts) -> int:
    """Qidiruvdagi takrorlanmas guruh + kanallar soni."""
    ids = set()
    for a in accounts:
        for g in SEARCH_CHATS.get(a["id"]) or GROUP_CACHE.get(a["id"], []):
            ids.add(g["chat_id"])
    return len(ids)


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
    c = _new_client()
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
    asyncio.create_task(_warm(aid, kind))


async def start_all():
    """Bot ishga tushganda saqlangan barcha akkauntlarni PARALLEL qayta ulaydi."""
    if not config.TG_API_ID or not config.TG_API_HASH:
        logging.warning("TG_API_ID/TG_API_HASH yo'q - userbotlar ishga tushirilmadi")
        return
    rows = [r for r in dbm.acc_list() if r["active"]]
    sem = asyncio.Semaphore(5)

    async def one(r):
        async with sem:
            await connect_row(r)

    await asyncio.gather(*(one(r) for r in rows))


async def connect_row(row) -> bool:
    aid = row["id"]
    try:
        c = _new_client(core.dec(row["session"]))
        await c.connect()
        if not await c.is_user_authorized():
            await c.disconnect()
            dbm.acc_update(aid, status="expired", active=0)
            msg = f"⚠️ Userbot sessiyasi tugagan: {row['name'] or row['phone']} ({row['kind']}). Qaytadan ulang."
            if row["kind"] == "user":
                await core.send(row["owner_id"], "⚠️ Ulangan Telegram akkauntingiz sessiyasi tugadi (avto xabar va "
                                                 "mijozlar bilan suhbat uchun). «📢 Avto xabar» bo'limidan qaytadan ulang.")
            else:
                await core.notify_admins(msg)
            return False
        await _activate(aid, row["kind"], c)
        return True
    except Exception as e:
        logging.exception("Akkauntni ulab bo'lmadi (#%s)", aid)
        dbm.acc_update(aid, status=f"xato: {e}"[:200])
        return False


async def watchdog_loop():
    """Har 90 soniyada: faol, lekin uzilib qolgan akkauntlarni qayta ulaydi. Pool uzilsa - adminga xabar."""
    while True:
        await asyncio.sleep(90)
        if not config.TG_API_ID or not config.TG_API_HASH:
            continue
        try:
            for row in dbm.acc_list(active_only=True):
                aid = row["id"]
                if is_connected(aid):
                    continue
                c = CLIENTS.get(aid)
                if c:
                    try:
                        await c.connect()
                        if c.is_connected() and await c.is_user_authorized():
                            continue
                    except Exception:
                        pass
                ok = await connect_row(row)
                if ok:
                    logging.info("Akkaunt qayta ulandi: #%s", aid)
                elif row["kind"] == "pool" and time.time() - _WD_NOTIFIED.get(aid, 0) > 3 * 3600:
                    _WD_NOTIFIED[aid] = time.time()
                    await core.notify_admins(f"🔴 Userbot #{aid} ({row['name'] or row['phone']}) uzilib qoldi va qayta "
                                             "ulanmayapti. 🛠 Admin panel → 🤖 Userbotlar dan tekshiring.")
        except Exception:
            logging.exception("Watchdog xatosi")


async def reconnect_all_pool() -> tuple:
    """Admin paneldan: barcha pool akkauntlarni qayta ulash. -> (ulandi, jami)"""
    rows = [r for r in dbm.acc_list("pool") if r["active"]]
    res = await asyncio.gather(*(connect_row(r) for r in rows))
    return sum(1 for x in res if x), len(rows)


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
    SEARCH_CHATS.pop(aid, None)
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
    if kind in ("pool", "user"):
        # pool va foydalanuvchi akkauntlari: mijozlar javobini AI suhbatga uzatamiz
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

async def _warm(aid: int, kind: str = ""):
    """Ulangandan keyin dialoglarni Telethon xotirasiga yuklaydi (guruhga yozish uchun kerak).
    Pool akkauntlar uchun guruhlar ro'yxati ham keshlanadi (qidiruv statistikasi uchun)."""
    try:
        if kind == "pool":
            await list_groups(aid)
        else:
            await CLIENTS[aid].get_dialogs(limit=500)
    except Exception:
        logging.warning("Dialoglarni yuklab bo'lmadi (#%s)", aid, exc_info=True)


def write_status(ent):
    """Akkaunt shu guruhga oddiy xabar yoza oladimi? -> (bool, sabab)"""
    if getattr(ent, "deactivated", False):
        return False, "guruh o'chirilgan"
    if getattr(ent, "creator", False) or getattr(ent, "admin_rights", None):
        return True, ""
    own = getattr(ent, "banned_rights", None)
    if own and (getattr(own, "send_messages", False) or getattr(own, "send_plain", False)):
        return False, "sizga yozish taqiqlangan"
    dflt = getattr(ent, "default_banned_rights", None)
    if dflt and (getattr(dflt, "send_messages", False) or getattr(dflt, "send_plain", False)):
        return False, "guruhda faqat adminlar yozadi"
    return True, ""


async def list_groups(aid: int) -> list:
    """Akkauntdagi barcha guruhlar + yozish mumkinmi. Yo'l-yo'lakay kanallar ham SEARCH_CHATS ga yoziladi."""
    out, chats = [], []
    async for d in CLIENTS[aid].iter_dialogs():
        if d.is_group:
            ok, why = write_status(d.entity)
            out.append({"chat_id": d.id, "title": d.name or str(d.id), "can_write": ok, "why": why})
            chats.append({"chat_id": d.id, "title": d.name or str(d.id)})
        elif d.is_channel:
            chats.append({"chat_id": d.id, "title": d.name or str(d.id)})
    GROUP_CACHE[aid] = out
    SEARCH_CHATS[aid] = chats
    return out


def _make_dict(m, s, chat, chat_id):
    """Xabar + yuboruvchi + chat -> lug'at. Oddiy foydalanuvchi yozmagan bo'lsa None."""
    text = (getattr(m, "message", None) or "").strip()
    if not text or not isinstance(s, types.User) or s.bot or s.deleted or s.is_self:
        return None
    uname = getattr(chat, "username", None)
    link = (f"https://t.me/{uname}/{m.id}" if uname
            else f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{m.id}")
    return {
        "msg_id": m.id, "chat_id": chat_id, "text": text, "date": m.date, "sender_id": s.id,
        "access_hash": s.access_hash, "username": s.username,
        "name": " ".join(x for x in (s.first_name, s.last_name) if x) or "Noma'lum",
        "link": link, "chat_title": getattr(chat, "title", "") or "",
    }


_UNAME_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})")


def _post_dict(m, chat, chat_id):
    """Kanal posti (yoki guruhga kanal nomidan yozilgan xabar): yuboruvchi - odam emas, shuning uchun
    matndagi @username yoki telefon kontakt sifatida olinadi. Kontakt bo'lmasa None."""
    text = (getattr(m, "message", None) or "").strip()
    if not text:
        return None
    own = (getattr(chat, "username", None) or "").lower()
    uname = next((u for u in _UNAME_RE.findall(text)
                  if u.lower() != own and not u.lower().endswith("bot")), None)
    phone = norm_phone(text)
    if not uname and not phone:
        return None
    contact = (uname or phone).lower()
    pseudo = -int(hashlib.sha1(contact.encode()).hexdigest()[:12], 16)  # odam ID si noma'lum - barqaror soxta ID
    cu = getattr(chat, "username", None)
    link = (f"https://t.me/{cu}/{m.id}" if cu else f"https://t.me/c/{str(chat_id).replace('-100', '', 1)}/{m.id}")
    title = getattr(chat, "title", "") or ""
    return {"msg_id": m.id, "chat_id": chat_id, "text": text, "date": m.date, "sender_id": pseudo,
            "access_hash": None, "username": uname, "name": (f"@{uname}" if uname else phone) + f" ({title[:20]})",
            "link": link, "chat_title": title}


def _msg_dict(m, chat_id=None):
    """Telethon (custom) xabari -> lug'at (odam yozgan xabar yoki kanal posti)."""
    cid = chat_id or m.chat_id
    if isinstance(m.sender, types.User):
        return _make_dict(m, m.sender, m.chat, cid)
    if m.sender is None or isinstance(m.sender, types.Channel):
        return _post_dict(m, m.chat, cid)
    return None


async def search_global(aid: int, query: str, since, limit: int = 400) -> list:
    """Telegram GLOBAL qidiruvi: akkaunt a'zo bo'lgan BARCHA guruh va kanallardan bitta so'rov bilan.
    30-50 ta chatni birma-bir o'qishdan o'nlab marta tez. Eng yangi xabarlardan boshlab `since` gacha.
    Kanal postlarida kontakt (@username / telefon) matndan olinadi."""
    c = CLIENTS[aid]
    out, got = [], 0
    rate, opeer, oid = 0, types.InputPeerEmpty(), 0
    while got < limit:
        r = await c(functions.messages.SearchGlobalRequest(
            q=query, filter=types.InputMessagesFilterEmpty(), min_date=since, max_date=None,
            offset_rate=rate, offset_peer=opeer, offset_id=oid, limit=100))
        msgs = getattr(r, "messages", None) or []
        if not msgs:
            break
        users = {u.id: u for u in r.users}
        chats = {ch.id: ch for ch in r.chats}
        stop = False
        for m in msgs:
            got += 1
            if not isinstance(m, types.Message):
                continue
            if m.date < since:
                stop = True
                break
            p = m.peer_id
            if isinstance(p, types.PeerChannel):
                chat = chats.get(p.channel_id)
            elif isinstance(p, types.PeerChat):
                chat = chats.get(p.chat_id)
            else:
                continue  # shaxsiy chatlar kerak emas
            cid = utils.get_peer_id(p)
            fid = getattr(m.from_id, "user_id", None)
            if fid:
                d = _make_dict(m, users.get(fid), chat, cid)
            else:
                d = _post_dict(m, chat, cid)
            if d:
                out.append(d)
        if stop or len(msgs) < 100 or not isinstance(r, types.messages.MessagesSlice):
            break
        last = msgs[-1]
        rate = getattr(r, "next_rate", None) or 0
        oid = last.id
        ch = chats.get(getattr(last.peer_id, "channel_id", None) or getattr(last.peer_id, "chat_id", None))
        try:
            opeer = utils.get_input_peer(ch) if ch else types.InputPeerEmpty()
        except Exception:
            break
        await asyncio.sleep(0.3)
    return out


async def fetch_messages(aid: int, chat_id: int, since, limit: int = 3000) -> list:
    """Guruhdan `since` (UTC) dan keyingi oddiy foydalanuvchilar yozgan xabarlar."""
    out = []
    async for m in CLIENTS[aid].iter_messages(chat_id, limit=limit, wait_time=1):
        if m.date < since:
            break
        d = _msg_dict(m, chat_id)
        if d:
            out.append(d)
    return out


async def search_messages(aid: int, query: str, since, limit: int = 200, chat=None) -> list:
    """Bitta guruh/kanal ichida Telegram qidiruvi (global qidiruv kam natija bersa - zaxira bosqich)."""
    out = []
    async for m in CLIENTS[aid].iter_messages(chat, search=query, limit=limit, wait_time=0.3):
        if m.date < since:
            break
        if chat is None and not m.is_group:
            continue
        d = _msg_dict(m)
        if d:
            out.append(d)
    return out


# ====================================================================== LICHKAGA YOZISH

def norm_phone(s):
    """E'londagi telefon -> +998901234567 ko'rinishi (o'qib bo'lmasa None)."""
    for m in re.finditer(r"\+?\d[\d\s\-()]{7,17}\d", s or ""):
        d = re.sub(r"\D", "", m.group(0))
        if len(d) == 9:
            d = "998" + d
        if 11 <= len(d) <= 13:
            return "+" + d
    return None


async def resolve_peer(aid: int, user_id: int, access_hash=None, username=None, phone=None, name=None):
    """Shu akkaunt uchun odamning InputPeerUser i: access_hash -> kesh -> @username -> telefon (kontaktga qo'shib).
    Topilmasa None."""
    c = CLIENTS[aid]
    if access_hash and user_id and user_id > 0:
        return types.InputPeerUser(user_id, access_hash)
    if user_id and user_id > 0:
        try:
            p = await c.get_input_entity(types.PeerUser(user_id))
            if isinstance(p, types.InputPeerUser):
                return p
        except (ValueError, TypeError, KeyError):
            pass
    if username:
        try:
            p = await c.get_input_entity(username)
            if isinstance(p, types.InputPeerUser):
                return p
        except (ValueError, TypeError, errors.UsernameNotOccupiedError, errors.UsernameInvalidError):
            pass
    ph = norm_phone(phone)
    if ph:
        try:
            r = await c(functions.contacts.ImportContactsRequest([types.InputPhoneContact(
                client_id=random.randrange(1, 2 ** 62), phone=ph, first_name=(name or "Mijoz")[:60], last_name="")]))
            if r.users:
                u = r.users[0]
                return types.InputPeerUser(u.id, u.access_hash)
        except errors.FloodWaitError:
            raise
        except Exception:
            logging.info("Telefon orqali topib bo'lmadi: %s", ph)
    return None


async def send_dm(aid: int, peer, text: str):
    """Odamga lichkada yozadi ("yozmoqda..." ko'rsatib). Telethon xatolari chaqiruvchida ushlanadi."""
    c = CLIENTS[aid]
    pid = getattr(peer, "user_id", peer)
    LAST_SEND[(aid, pid)] = time.time()
    try:
        async with c.action(peer, "typing"):
            await asyncio.sleep(min(7.0, 1.0 + len(text) / 30))
        msg = await c.send_message(peer, text)
    finally:
        LAST_SEND[(aid, pid)] = time.time()
    return msg
