"""E'LON KUZATUVI: Autoline, Otomoto, mobile.de dagi yangi tyagach e'lonlari -> faqat ADMINGA.

Qanday ishlaydi (saytlarning o'z xizmati orqali, hech qanday qoida buzilmaydi):
 1. Admin saytlarda «saqlangan qidiruv / obuna» qiladi (masalan tyagach, 2022+) - saytlar yangi e'lonlarni
    Gmail ga xat qilib yuboradi.
 2. Bot shu Gmail ga Google «ilova paroli» bilan IMAP orqali ulanadi (faqat o'qiydi, xatlarni o'zgartirmaydi,
    o'qilgan deb ham belgilamaydi).
 3. Har `lm_interval` daqiqada yangi xatlar olinadi, ichidan har bir e'lon ajratiladi (arzon AI, xat formati
    o'zgarsa ham ishlaydi), yili `lm_year` dan past e'lonlar va takrorlar tashlab yuboriladi.
 4. Har bir e'lon adminga rasm + qisqa ma'lumot + «E'lonni ochish» tugmasi bilan yuboriladi.
"""
import asyncio
import email
import email.policy
import hashlib
import imaplib
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup as K

import config
import core
import database as dbm
import llm

SOURCES = {"autoline": "Autoline", "otomoto": "Otomoto", "mobile.de": "mobile.de"}
IMAP_HOST = "imap.gmail.com"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_LOCK = asyncio.Lock()
_NOTIFIED: dict = {}
STATE = {"last_check": None, "last_error": "", "sent_session": 0}


# ====================================================================== SOZLAMALAR VA BAZA

def _init():
    with dbm.db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS lm_seen(id TEXT PRIMARY KEY, kind TEXT, created TEXT)")


def cfg() -> dict:
    s = dbm.settings()
    pwd = ""
    try:
        pwd = core.dec(s.get("lm_pass") or "") or ""
    except Exception:
        pwd = ""
    return {"email": (s.get("lm_email") or "").strip(), "pass": pwd.replace(" ", ""),
            "on": s.get("lm_on") == "1", "year": int(s.get("lm_year") or 2022),
            "interval": max(3, int(s.get("lm_interval") or 5))}


def _seen(key) -> bool:
    with dbm.db() as c:
        return c.execute("SELECT 1 FROM lm_seen WHERE id=?", (key,)).fetchone() is not None


def _mark(key, kind):
    with dbm.db() as c:
        c.execute("INSERT OR IGNORE INTO lm_seen(id, kind, created) VALUES(?,?,?)", (key, kind, dbm.now_iso()))


def stats() -> dict:
    _init()
    today = dbm.today().isoformat()
    with dbm.db() as c:
        sent_today = c.execute("SELECT COUNT(*) FROM lm_seen WHERE kind='ad' AND created>=?", (today,)).fetchone()[0]
        mails_today = c.execute("SELECT COUNT(*) FROM lm_seen WHERE kind='mail' AND created>=?", (today,)).fetchone()[0]
        total = c.execute("SELECT COUNT(*) FROM lm_seen WHERE kind='ad'").fetchone()[0]
    return {"sent_today": sent_today, "mails_today": mails_today, "total": total}


def cleanup(days=60):
    cutoff = (datetime.now(config.TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    with dbm.db() as c:
        c.execute("DELETE FROM lm_seen WHERE created<?", (cutoff,))


# ====================================================================== GMAIL (IMAP)

def _imap_login(addr, pwd):
    m = imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=60)
    m.login(addr, pwd)
    return m


def test_login(addr, pwd):
    """-> (True, "") yoki (False, sabab)"""
    try:
        m = _imap_login(addr, pwd.replace(" ", ""))
        m.logout()
        return True, ""
    except imaplib.IMAP4.error as e:
        return False, _imap_reason(e)
    except Exception as e:
        return False, f"ulanib bo'lmadi: {e}"


def _imap_reason(e) -> str:
    t = str(e).lower()
    if "application-specific" in t or "authenticationfailed" in t or "invalid credentials" in t:
        return "email yoki ilova paroli noto'g'ri (Google «ilova paroli» kerak, oddiy parol emas)"
    if "imap" in t and "disabled" in t:
        return "Gmail sozlamalarida IMAP o'chirilgan"
    return str(e)[:150]


def _since_str(days):
    d = date.today() - timedelta(days=days)
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def _fetch_new(addr, pwd, days=3, limit_per_source=40) -> list:
    """Saytlardan kelgan, hali ko'rilmagan xatlar: [{"key","source","subject","html","text"}]."""
    out = []
    m = _imap_login(addr, pwd)
    try:
        m.select("INBOX", readonly=True)
        since = _since_str(days)
        for needle, name in SOURCES.items():
            typ, data = m.uid("search", None, "SINCE", since, "FROM", f'"{needle}"')
            if typ != "OK" or not data or not data[0]:
                continue
            for uid in data[0].split()[-limit_per_source:]:
                typ, h = m.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
                raw_h = b"".join(x[1] for x in h if isinstance(x, tuple)) if typ == "OK" else b""
                mid = re.search(rb"<([^>]+)>", raw_h)
                key = "m:" + (mid.group(1).decode("utf-8", "ignore") if mid else f"{needle}:{uid.decode()}")
                if _seen(key):
                    continue
                typ, d = m.uid("fetch", uid, "(BODY.PEEK[])")
                if typ != "OK":
                    continue
                raw = b"".join(x[1] for x in d if isinstance(x, tuple))
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                h_part, t_part = "", ""
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if part.get_content_maintype() == "multipart" or part.get_filename():
                        continue
                    try:
                        content = part.get_content()
                    except Exception:
                        continue
                    if ctype == "text/html" and not h_part:
                        h_part = content
                    elif ctype == "text/plain" and not t_part:
                        t_part = content
                out.append({"key": key, "source": name, "subject": str(msg.get("Subject") or ""),
                            "html": h_part, "text": t_part})
    finally:
        try:
            m.logout()
        except Exception:
            pass
    return out


# ====================================================================== XATDAN E'LONLARNI AJRATISH

class _Html2Text(HTMLParser):
    """HTML -> havolalar va rasmlar saqlangan oddiy matn: [matn](havola), ![](rasm)."""

    BLOCK = {"p", "div", "tr", "table", "br", "li", "h1", "h2", "h3", "h4", "td"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.href, self.atext = [], 0, None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "script", "head"):
            self.skip += 1
        elif tag == "a":
            self.href, self.atext = a.get("href"), []
        elif tag == "img":
            src, w, h = a.get("src") or "", a.get("width") or "", a.get("height") or ""
            if src.startswith("http") and w not in ("1", "0") and h not in ("1", "0") and "track" not in src.lower():
                self._emit(f" ![]({src}) ")
        elif tag in self.BLOCK:
            self._emit("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag == "a" and self.href is not None:
            text = " ".join("".join(self.atext).split())
            if self.href.startswith("http"):
                self.out.append(f" [{text}]({self.href}) ")
            else:
                self.out.append(f" {text} ")
            self.href, self.atext = None, []
        elif tag in self.BLOCK:
            self._emit("\n")

    def handle_data(self, data):
        if self.skip:
            return
        if self.href is not None:
            self.atext.append(data)
        else:
            self._emit(data)

    def _emit(self, s):
        if self.href is not None:
            self.atext.append(s)
        else:
            self.out.append(s)

    def text(self):
        t = "".join(self.out)
        t = re.sub(r"[ \t ]+", " ", t)
        return re.sub(r"\n\s*\n+", "\n", t).strip()


def email_to_text(mail) -> str:
    if mail.get("html"):
        p = _Html2Text()
        try:
            p.feed(mail["html"])
            return p.text()
        except Exception:
            pass
    return (mail.get("text") or "").strip()


EXTRACT_SYSTEM = ("Siz avtomobil e'lonlari saytlari (Autoline, Otomoto, mobile.de) yuborgan «yangi e'lonlar» "
                  "xabarnoma xatlaridan e'lonlarni ajratuvchisiz. Faqat JSON qaytaring.")


def _extract_prompt(mail, text) -> str:
    return f"""Sayt: {mail['source']}
Xat mavzusi: {mail['subject']}
Xat matni (havolalar [matn](url), rasmlar ![](url) ko'rinishida):
\"\"\"
{text[:14000]}
\"\"\"

Xatdagi HAR BIR transport e'lonini ajrating. Faqat haqiqiy e'lonlar: reklama, «barchasini ko'rish», «obunani
bekor qilish», ijtimoiy tarmoq va sozlamalar havolalarini qo'shmang.

FAQAT JSON:
{{"ads": [{{"title": "e'lon sarlavhasi (marka model)", "make": "marka yoki null", "model": "model yoki null",
  "year": ishlab chiqarilgan yoki ro'yxatga olingan yil (butun son) yoki null,
  "mileage": "probeg, masalan 450 000 km, yoki null", "price": "narx valyutasi bilan, masalan 37 500 EUR, yoki null",
  "location": "mamlakat/shahar yoki null", "power": "quvvati (masalan 480 л.с.) yoki null",
  "url": "shu e'lonning havolasi - matndagi aniq havola, o'zgartirmasdan",
  "image": "shu e'lon rasmi havolasi yoki null"}}]}}"""


def _norm_url(u: str) -> str:
    u = (u or "").strip()
    ids = re.findall(r"\d{6,}", u)
    if ids:
        return re.sub(r"^https?://(www\.)?", "", u).split("/")[0] + ":" + max(ids, key=len)
    return u.split("?")[0]


def _ad_key(ad) -> str:
    base = _norm_url(ad.get("url")) if ad.get("url") else ""
    if not base:
        base = f"{ad.get('title')}|{ad.get('price')}|{ad.get('mileage')}|{ad.get('year')}"
    return "a:" + hashlib.sha1(base.lower().encode()).hexdigest()


def _year(ad):
    y = ad.get("year")
    try:
        y = int(str(y)[:4])
        if 1980 <= y <= date.today().year + 1:
            return y
    except (TypeError, ValueError):
        pass
    m = re.search(r"\b(20[0-3]\d|19[89]\d)\b", f"{ad.get('title') or ''} {ad.get('model') or ''}")
    return int(m.group(1)) if m else None


async def extract_ads(mail) -> list:
    text = email_to_text(mail)
    if len(text) < 30:
        return []
    raw = await llm.cheap(EXTRACT_SYSTEM, [{"role": "user", "content": _extract_prompt(mail, text)}], 4000)
    a, b = raw.find("{"), raw.rfind("}")
    data = json.loads(raw[a:b + 1])
    ads = [x for x in (data.get("ads") or []) if isinstance(x, dict) and (x.get("title") or x.get("url"))]
    for x in ads:
        x["year"] = _year(x)
        x["source"] = mail["source"]
        if x.get("url") and not str(x["url"]).startswith("http"):
            x["url"] = None
        if x.get("image") and not str(x["image"]).startswith("http"):
            x["image"] = None
    return ads


# ====================================================================== ADMINGA YUBORISH

def card(ad) -> str:
    esc = core.esc
    title = (ad.get("title") or "Yangi e'lon")[:120]
    lines = [f"🚛 <b>{esc(title)}</b>", ""]
    row = []
    if ad.get("year"):
        row.append(f"📅 {ad['year']}")
    if ad.get("mileage"):
        row.append(f"🛣 {esc(ad['mileage'])}")
    if ad.get("power"):
        row.append(f"⚡ {esc(ad['power'])}")
    if row:
        lines.append(" · ".join(row))
    if ad.get("price"):
        lines.append(f"💰 <b>{esc(ad['price'])}</b>")
    if ad.get("location"):
        lines.append(f"📍 {esc(ad['location'])}")
    lines.append(f"🌐 {esc(ad.get('source') or '')}")
    return "\n".join(lines)


async def _send_to_admins(ad):
    text = card(ad)
    kb = K(inline_keyboard=[[B(text="🔗 E'lonni ochish", url=ad["url"])]]) if ad.get("url") else None
    for aid in dbm.admin_ids():
        sent = False
        if ad.get("image"):
            try:
                await core.bot.send_photo(aid, ad["image"], caption=text, parse_mode="HTML", reply_markup=kb)
                sent = True
            except Exception:
                sent = False
        if not sent:
            try:
                await core.bot.send_message(aid, text, parse_mode="HTML", reply_markup=kb,
                                            disable_web_page_preview=True)
            except Exception:
                logging.warning("E'lonni adminga yuborib bo'lmadi (%s)", aid, exc_info=True)
        await asyncio.sleep(0.15)


async def _notify_once(key, text, every=6 * 3600):
    if time.time() - _NOTIFIED.get(key, 0) < every:
        return
    _NOTIFIED[key] = time.time()
    await core.notify_admins(text)


# ====================================================================== ASOSIY TEKSHIRUV

async def check_now(manual=False) -> dict:
    """Bir marta tekshiradi. -> {"mails", "ads", "sent", "skipped_year", "error"}"""
    _init()
    c = cfg()
    res = {"mails": 0, "ads": 0, "sent": 0, "skipped_year": 0, "error": ""}
    if not c["email"] or not c["pass"]:
        res["error"] = "Gmail ulanmagan"
        return res
    if _LOCK.locked():
        res["error"] = "tekshiruv allaqachon ketyapti"
        return res
    async with _LOCK:
        try:
            mails = await asyncio.to_thread(_fetch_new, c["email"], c["pass"])
        except imaplib.IMAP4.error as e:
            res["error"] = _imap_reason(e)
        except Exception as e:
            res["error"] = f"Gmail ga ulanib bo'lmadi: {e}"[:200]
        if res["error"]:
            STATE["last_error"] = res["error"]
            STATE["last_check"] = dbm.now_iso()
            await _notify_once("lm_err", f"⚠️ E'lon kuzatuvi: {res['error']}\n🛠 Admin panel → 🚛 E'lon kuzatuvi")
            return res
        res["mails"] = len(mails)
        for mail in mails:
            try:
                ads = await extract_ads(mail)
            except Exception:
                logging.warning("Xatdan e'lonlarni ajratib bo'lmadi (%s)", mail["subject"], exc_info=True)
                continue  # xat «ko'rilgan» deb belgilanmaydi - keyingi safar qayta urinadi
            for ad in ads[:40]:
                key = _ad_key(ad)
                if _seen(key):
                    continue
                if ad.get("year") and ad["year"] < c["year"]:
                    _mark(key, "skip")
                    res["skipped_year"] += 1
                    continue
                res["ads"] += 1
                await _send_to_admins(ad)
                _mark(key, "ad")
                res["sent"] += 1
            _mark(mail["key"], "mail")
        STATE["last_check"] = dbm.now_iso()
        STATE["last_error"] = ""
        STATE["sent_session"] += res["sent"]
        if res["sent"]:
            logging.info("E'lon kuzatuvi: %s xat, %s e'lon yuborildi", res["mails"], res["sent"])
    return res


async def loop():
    await asyncio.sleep(30)
    n = 0
    while True:
        c = cfg()
        try:
            if c["on"] and c["email"] and c["pass"]:
                await check_now()
            if n % 288 == 0:
                cleanup()
        except Exception:
            logging.exception("E'lon kuzatuvi siklida xato")
        n += 1
        await asyncio.sleep(c["interval"] * 60)
