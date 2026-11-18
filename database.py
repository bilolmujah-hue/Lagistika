"""Ma'lumotlar bazasi (SQLite). Har bir foydalanuvchi faqat o'z ma'lumotini ko'radi (user_id bo'yicha)."""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, name TEXT, created TEXT);

CREATE TABLE IF NOT EXISTS vehicles(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, plate TEXT NOT NULL,
    model TEXT, vin TEXT, driver_id INTEGER, china_queue_date TEXT, created TEXT,
    UNIQUE(user_id, plate));

CREATE TABLE IF NOT EXISTS drivers(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL,
    phone TEXT, created TEXT);

CREATE TABLE IF NOT EXISTS documents(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    owner_type TEXT NOT NULL, owner_id INTEGER NOT NULL, doc_type TEXT NOT NULL,
    doc_number TEXT, issue_date TEXT, expiry_date TEXT, issuer TEXT, notes TEXT,
    file_id TEXT, file_kind TEXT, created TEXT);
CREATE INDEX IF NOT EXISTS idx_docs_owner ON documents(user_id, owner_type, owner_id);

CREATE TABLE IF NOT EXISTS reminders(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
    title TEXT, remind_date TEXT NOT NULL, vehicle_id INTEGER, note TEXT,
    pre_days INTEGER DEFAULT 1, done INTEGER DEFAULT 0, created TEXT);

CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    role TEXT NOT NULL, content TEXT NOT NULL, created TEXT);

CREATE TABLE IF NOT EXISTS sent(key TEXT PRIMARY KEY, sent_at TEXT);

CREATE TABLE IF NOT EXISTS ub_sessions(
    user_id INTEGER PRIMARY KEY, phone TEXT, session TEXT, created TEXT);

CREATE TABLE IF NOT EXISTS ub_groups(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
    title TEXT, UNIQUE(user_id, chat_id));

CREATE TABLE IF NOT EXISTS cargo_requests(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, origin TEXT, destination TEXT,
    note TEXT, hours INTEGER, status TEXT DEFAULT 'searching', scanned INTEGER DEFAULT 0,
    found INTEGER DEFAULT 0, created TEXT);

CREATE TABLE IF NOT EXISTS leads(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, request_id INTEGER,
    sender_id INTEGER NOT NULL, access_hash INTEGER, username TEXT, name TEXT, phone TEXT,
    origin TEXT, destination TEXT, cargo TEXT, ad_text TEXT, ad_link TEXT,
    status TEXT DEFAULT 'new', info TEXT, summary TEXT, contacted_at TEXT, created TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS idx_leads_sender ON leads(user_id, sender_id);

CREATE TABLE IF NOT EXISTS lead_msgs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL, role TEXT NOT NULL,
    content TEXT NOT NULL, created TEXT);
"""

STATUS_EMOJI = {
    "missing": "⚪", "noexp": "🔵", "expired": "🔴", "red": "🔴", "yellow": "🟡", "green": "🟢",
}


@contextmanager
def db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as c:
        c.executescript(SCHEMA)


# ------------------------------------------------------------------ yordamchilar

def now_iso() -> str:
    return datetime.now(config.TZ).isoformat(timespec="seconds")


def today() -> date:
    return datetime.now(config.TZ).date()


_CYR2LAT = str.maketrans("АВЕКМНОРСТХУ", "ABEKMHOPCTXY")


def norm_plate(s) -> str:
    """Davlat raqamini bir xil ko'rinishga keltiradi: bo'shliqsiz, katta harf, kirill -> lotin."""
    s = (s or "").upper().translate(_CYR2LAT)
    return re.sub(r"[^A-Z0-9]", "", s)


def norm_name(s) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def parse_date(s):
    """'2027-10-06' yoki '06.10.2027' -> '2027-10-06'. Noto'g'ri bo'lsa None."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def fmt_date(iso) -> str:
    if not iso:
        return "-"
    try:
        return date.fromisoformat(iso).strftime("%d.%m.%Y")
    except ValueError:
        return str(iso)


def days_left(expiry_iso):
    if not expiry_iso:
        return None
    try:
        return (date.fromisoformat(expiry_iso) - today()).days
    except ValueError:
        return None


def doc_status(row):
    """(holat, qolgan_kun). Holat: missing / noexp / expired / red / yellow / green."""
    if row is None:
        return "missing", None
    d = days_left(row.get("expiry_date"))
    if d is None:
        return "noexp", None
    if d < 0:
        return "expired", d
    if d <= config.RED_DAYS:
        return "red", d
    if d <= config.YELLOW_DAYS:
        return "yellow", d
    return "green", d


# ------------------------------------------------------------------ foydalanuvchilar

def ensure_user(user_id, name):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id, name, created) VALUES(?,?,?)",
                  (user_id, name, now_iso()))


def all_user_ids():
    with db() as c:
        return [r["user_id"] for r in c.execute("SELECT user_id FROM users")]


# ------------------------------------------------------------------ furalar

def get_or_create_vehicle(uid, plate, model=None, vin=None):
    p = norm_plate(plate)
    if not p:
        return None
    with db() as c:
        r = c.execute("SELECT * FROM vehicles WHERE user_id=? AND plate=?", (uid, p)).fetchone()
        if r:
            if (model and not r["model"]) or (vin and not r["vin"]):
                c.execute("UPDATE vehicles SET model=COALESCE(model,?), vin=COALESCE(vin,?) WHERE id=?",
                          (model, vin, r["id"]))
            return r["id"]
        cur = c.execute(
            "INSERT INTO vehicles(user_id, plate, model, vin, created) VALUES(?,?,?,?,?)",
            (uid, p, model, vin, now_iso()))
        return cur.lastrowid


def get_vehicle(uid, vid):
    with db() as c:
        r = c.execute("SELECT * FROM vehicles WHERE id=? AND user_id=?", (vid, uid)).fetchone()
        return dict(r) if r else None


def find_vehicle(uid, query):
    p = norm_plate(query)
    if not p:
        return None
    with db() as c:
        r = c.execute("SELECT * FROM vehicles WHERE user_id=? AND plate=?", (uid, p)).fetchone()
        if not r:
            r = c.execute("SELECT * FROM vehicles WHERE user_id=? AND plate LIKE ? ORDER BY plate",
                          (uid, f"%{p}%")).fetchone()
        return dict(r) if r else None


def list_vehicles(uid):
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM vehicles WHERE user_id=? ORDER BY plate", (uid,))]


# ------------------------------------------------------------------ haydovchilar

def get_or_create_driver(uid, name, phone=None):
    name = norm_name(name)
    if not name:
        return None
    key = name.casefold()
    with db() as c:
        for r in c.execute("SELECT * FROM drivers WHERE user_id=?", (uid,)):
            if r["name"].casefold() == key:
                if phone and not r["phone"]:
                    c.execute("UPDATE drivers SET phone=? WHERE id=?", (phone, r["id"]))
                return r["id"]
        cur = c.execute("INSERT INTO drivers(user_id, name, phone, created) VALUES(?,?,?,?)",
                        (uid, name, phone, now_iso()))
        return cur.lastrowid


def get_driver(uid, did):
    with db() as c:
        r = c.execute("SELECT * FROM drivers WHERE id=? AND user_id=?", (did, uid)).fetchone()
        return dict(r) if r else None


def find_driver(uid, query):
    q = norm_name(query).casefold()
    if not q:
        return None
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM drivers WHERE user_id=?", (uid,))]
    for r in rows:
        if r["name"].casefold() == q:
            return r
    for r in rows:
        if q in r["name"].casefold():
            return r
    # ism tartibi boshqacha bo'lsa ham topish (masalan "Aliyev Vali" / "Vali Aliyev")
    words = set(q.split())
    for r in rows:
        if words and words <= set(r["name"].casefold().split()):
            return r
    return None


def list_drivers(uid):
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM drivers WHERE user_id=? ORDER BY name", (uid,))]


def assign_driver(uid, driver_id, vehicle_id):
    with db() as c:
        c.execute("UPDATE vehicles SET driver_id=? WHERE id=? AND user_id=?",
                  (driver_id, vehicle_id, uid))


def vehicles_of_driver(uid, driver_id):
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM vehicles WHERE user_id=? AND driver_id=?", (uid, driver_id))]


# ------------------------------------------------------------------ hujjatlar

def save_document(uid, owner_type, owner_id, doc_type, number=None, issue=None, expiry=None,
                  issuer=None, notes=None, file_id=None, file_kind=None):
    """Bir xil turdagi eski hujjat yangisi bilan almashtiriladi (plastik kartadan tashqari)."""
    with db() as c:
        base = "DELETE FROM documents WHERE user_id=? AND owner_type=? AND owner_id=? AND doc_type=?"
        args = [uid, owner_type, owner_id, doc_type]
        if doc_type in config.MULTI_DOC_TYPES:
            if number:
                c.execute(base + " AND doc_number=?", args + [number])
        else:
            c.execute(base, args)
        cur = c.execute(
            """INSERT INTO documents(user_id, owner_type, owner_id, doc_type, doc_number, issue_date,
               expiry_date, issuer, notes, file_id, file_kind, created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, owner_type, owner_id, doc_type, number, issue, expiry, issuer, notes,
             file_id, file_kind, now_iso()))
        return cur.lastrowid


def _collapse(rows):
    """Bir egasining hujjatlarini {tur: hujjat} ko'rinishiga keltiradi."""
    out = {}
    for r in rows:
        t = r["doc_type"]
        cur = out.get(t)
        if cur is None:
            out[t] = r
        elif t in config.MULTI_DOC_TYPES:
            # bir nechta bo'lsa, muddati eng yaqinini ko'rsatamiz
            if r["expiry_date"] and (not cur["expiry_date"] or r["expiry_date"] < cur["expiry_date"]):
                out[t] = r
        else:
            out[t] = r
    return out


def owner_docs(uid, owner_type, owner_id):
    with db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM documents WHERE user_id=? AND owner_type=? AND owner_id=? ORDER BY id",
            (uid, owner_type, owner_id))]
    return _collapse(rows)


def docs_by_owner(uid, owner_type):
    """{egasi_id: {tur: hujjat}} - jadval uchun."""
    with db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM documents WHERE user_id=? AND owner_type=? ORDER BY id",
            (uid, owner_type))]
    grouped = {}
    for r in rows:
        grouped.setdefault(r["owner_id"], []).append(r)
    return {oid: _collapse(lst) for oid, lst in grouped.items()}


def get_document(uid, doc_id):
    with db() as c:
        r = c.execute("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, uid)).fetchone()
        return dict(r) if r else None


def update_document(uid, doc_id, field, value):
    allowed = {"doc_number", "issue_date", "expiry_date", "issuer", "notes"}
    if field not in allowed:
        return None
    if field in ("issue_date", "expiry_date"):
        value = parse_date(value)
        if value is None:
            return None
    with db() as c:
        r = c.execute("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, uid)).fetchone()
        if not r:
            return None
        old = r[field]
        c.execute(f"UPDATE documents SET {field}=? WHERE id=?", (value, doc_id))
    return {"old": old, "new": value}


def delete_document(uid, doc_id):
    with db() as c:
        cur = c.execute("DELETE FROM documents WHERE id=? AND user_id=?", (doc_id, uid))
        return cur.rowcount > 0


def expiring(uid, within_days=30, owner_type=None):
    """Muddati within_days kun ichida tugaydigan yoki o'tib ketgan hujjatlar (eng yaqini birinchi)."""
    q = "SELECT * FROM documents WHERE user_id=? AND expiry_date IS NOT NULL"
    args = [uid]
    if owner_type in ("vehicle", "driver"):
        q += " AND owner_type=?"
        args.append(owner_type)
    with db() as c:
        rows = [dict(r) for r in c.execute(q, args)]
        vmap = {r["id"]: dict(r) for r in c.execute("SELECT * FROM vehicles WHERE user_id=?", (uid,))}
        dmap = {r["id"]: dict(r) for r in c.execute("SELECT * FROM drivers WHERE user_id=?", (uid,))}
    out = []
    for r in rows:
        d = days_left(r["expiry_date"])
        if d is None or d > within_days:
            continue
        if r["owner_type"] == "vehicle":
            o = vmap.get(r["owner_id"])
            r["owner_name"] = o["plate"] if o else "?"
            r["owner_icon"] = "🚛"
        else:
            o = dmap.get(r["owner_id"])
            r["owner_name"] = o["name"] if o else "?"
            r["owner_icon"] = "👤"
        r["doc_label"] = config.ALL_DOCS.get(r["doc_type"], r["doc_type"])
        r["days_left"] = d
        out.append(r)
    out.sort(key=lambda x: x["days_left"])
    return out


# ------------------------------------------------------------------ eslatmalar

def reminder_title(r) -> str:
    base = config.REMINDER_LABELS.get(r["kind"], "Eslatma")
    if r.get("title") and r["kind"] in ("custom", "debt", "uz_entry"):
        return f"{base}: {r['title']}" if r["kind"] != "custom" else r["title"]
    return base


def add_reminder(uid, kind, title, remind_date, vehicle_id=None, note=None, pre_days=1):
    with db() as c:
        cur = c.execute(
            """INSERT INTO reminders(user_id, kind, title, remind_date, vehicle_id, note, pre_days, created)
               VALUES(?,?,?,?,?,?,?,?)""",
            (uid, kind, title, remind_date, vehicle_id, note, pre_days, now_iso()))
        return cur.lastrowid


def set_china_queue(uid, vehicle_id, date_iso):
    """Fura uchun Xitoy navbati sanasini yozadi va 2 ta avtomatik eslatma yaratadi:
    Xitoyga kirish navbati (shu sanadan), Rossiyaga chiqish navbati (3 kun keyin)."""
    d = date.fromisoformat(date_iso)
    with db() as c:
        c.execute("UPDATE vehicles SET china_queue_date=? WHERE id=? AND user_id=?",
                  (date_iso, vehicle_id, uid))
        c.execute("""DELETE FROM reminders WHERE user_id=? AND vehicle_id=? AND done=0
                     AND kind IN ('china_entry','russia_exit')""", (uid, vehicle_id))
    add_reminder(uid, "china_entry", None, date_iso, vehicle_id, pre_days=0)
    add_reminder(uid, "russia_exit", None,
                 (d + timedelta(days=config.RUSSIA_AFTER_CHINA_DAYS)).isoformat(),
                 vehicle_id, pre_days=0)


def list_reminders(uid, only_open=True):
    q = ("SELECT r.*, v.plate AS plate FROM reminders r "
         "LEFT JOIN vehicles v ON v.id=r.vehicle_id WHERE r.user_id=?")
    if only_open:
        q += " AND r.done=0"
    q += " ORDER BY r.remind_date"
    with db() as c:
        return [dict(r) for r in c.execute(q, (uid,))]


def due_reminders(uid, today_date):
    out = []
    for r in list_reminders(uid, only_open=True):
        d = date.fromisoformat(r["remind_date"])
        if d - timedelta(days=r["pre_days"] or 0) <= today_date:
            out.append(r)
    return out


def mark_reminder_done(uid, rid):
    with db() as c:
        cur = c.execute("UPDATE reminders SET done=1 WHERE id=? AND user_id=?", (rid, uid))
        return cur.rowcount > 0


# ------------------------------------------------------------------ tahrirlash / o'chirish (yangi)

def owner_doc_rows(uid, owner_type, owner_id):
    """Egasining BARCHA hujjat qatorlari (plastik karta kabi bir nechtasi bo'lsa ham)."""
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM documents WHERE user_id=? AND owner_type=? AND owner_id=? ORDER BY id",
            (uid, owner_type, owner_id))]


def delete_documents_of_type(uid, owner_type, owner_id, doc_type):
    """Egasining shu turdagi hujjatlarini o'chiradi. O'chirilgan soni qaytadi."""
    with db() as c:
        cur = c.execute(
            "DELETE FROM documents WHERE user_id=? AND owner_type=? AND owner_id=? AND doc_type=?",
            (uid, owner_type, owner_id, doc_type))
        return cur.rowcount


def update_vehicle(uid, vid, field, value):
    """plate / model / vin ni o'zgartiradi. -> {'old','new'} | {'error':...} | None (fura topilmadi)."""
    if field not in ("plate", "model", "vin"):
        return {"error": "Faqat plate, model yoki vin o'zgartiriladi"}
    with db() as c:
        r = c.execute("SELECT * FROM vehicles WHERE id=? AND user_id=?", (vid, uid)).fetchone()
        if not r:
            return None
        if field == "plate":
            value = norm_plate(value)
            if not value:
                return {"error": "Fura raqami noto'g'ri"}
            dup = c.execute("SELECT id FROM vehicles WHERE user_id=? AND plate=? AND id<>?",
                            (uid, value, vid)).fetchone()
            if dup:
                return {"error": f"{value} raqamli fura allaqachon bor"}
        else:
            value = (value or "").strip() or None
        old = r[field]
        c.execute(f"UPDATE vehicles SET {field}=? WHERE id=?", (value, vid))
    return {"old": old, "new": value}


def update_driver(uid, did, field, value):
    """name / phone ni o'zgartiradi. -> {'old','new'} | {'error':...} | None (topilmadi)."""
    if field not in ("name", "phone"):
        return {"error": "Faqat name yoki phone o'zgartiriladi"}
    with db() as c:
        r = c.execute("SELECT * FROM drivers WHERE id=? AND user_id=?", (did, uid)).fetchone()
        if not r:
            return None
        value = norm_name(value) if field == "name" else ((value or "").strip() or None)
        if field == "name" and not value:
            return {"error": "Ism bo'sh bo'lishi mumkin emas"}
        old = r[field]
        c.execute(f"UPDATE drivers SET {field}=? WHERE id=?", (value, did))
    return {"old": old, "new": value}


def unassign_driver(uid, vehicle_id):
    with db() as c:
        cur = c.execute("UPDATE vehicles SET driver_id=NULL WHERE id=? AND user_id=?", (vehicle_id, uid))
        return cur.rowcount > 0


def delete_vehicle(uid, vid):
    """Furani va uning hujjatlari/eslatmalarini o'chiradi. -> {'hujjat': n, 'eslatma': n} | None"""
    with db() as c:
        if not c.execute("SELECT 1 FROM vehicles WHERE id=? AND user_id=?", (vid, uid)).fetchone():
            return None
        nd = c.execute("DELETE FROM documents WHERE user_id=? AND owner_type='vehicle' AND owner_id=?",
                       (uid, vid)).rowcount
        nr = c.execute("DELETE FROM reminders WHERE user_id=? AND vehicle_id=?", (uid, vid)).rowcount
        c.execute("DELETE FROM vehicles WHERE id=? AND user_id=?", (vid, uid))
    return {"hujjat": nd, "eslatma": nr}


def delete_driver(uid, did):
    """Haydovchini va hujjatlarini o'chiradi, furalardan ajratadi. -> {'hujjat': n} | None"""
    with db() as c:
        if not c.execute("SELECT 1 FROM drivers WHERE id=? AND user_id=?", (did, uid)).fetchone():
            return None
        c.execute("UPDATE vehicles SET driver_id=NULL WHERE user_id=? AND driver_id=?", (uid, did))
        nd = c.execute("DELETE FROM documents WHERE user_id=? AND owner_type='driver' AND owner_id=?",
                       (uid, did)).rowcount
        c.execute("DELETE FROM drivers WHERE id=? AND user_id=?", (did, uid))
    return {"hujjat": nd}


def get_reminder(uid, rid):
    with db() as c:
        r = c.execute("""SELECT r.*, v.plate AS plate FROM reminders r
                         LEFT JOIN vehicles v ON v.id=r.vehicle_id
                         WHERE r.id=? AND r.user_id=?""", (rid, uid)).fetchone()
        return dict(r) if r else None


def update_reminder(uid, rid, field, value):
    """remind_date (sana) yoki title (matn). -> {'old','new'} | None"""
    if field not in ("remind_date", "title"):
        return None
    if field == "remind_date":
        value = parse_date(value)
        if not value:
            return None
    with db() as c:
        r = c.execute("SELECT * FROM reminders WHERE id=? AND user_id=?", (rid, uid)).fetchone()
        if not r:
            return None
        old = r[field]
        c.execute(f"UPDATE reminders SET {field}=? WHERE id=?", (value, rid))
        if field == "remind_date":  # sana o'zgarsa, eslatma qaytadan yuborilishi uchun
            c.execute("DELETE FROM sent WHERE key LIKE ?", (f"rem:{rid}:%",))
    return {"old": old, "new": value}


def delete_reminder(uid, rid):
    with db() as c:
        cur = c.execute("DELETE FROM reminders WHERE id=? AND user_id=?", (rid, uid))
        return cur.rowcount > 0


# ------------------------------------------------------------------ suhbat tarixi

def save_message(uid, role, content):
    with db() as c:
        c.execute("INSERT INTO messages(user_id, role, content, created) VALUES(?,?,?,?)",
                  (uid, role, content, now_iso()))


def load_history(uid, limit=12):
    with db() as c:
        rows = c.execute("SELECT role, content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                         (uid, limit)).fetchall()
    msgs = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


# ------------------------------------------------------------------ yuborilgan eslatmalar jurnali

def was_sent(key) -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM sent WHERE key=?", (key,)).fetchone() is not None


def mark_sent(key):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO sent(key, sent_at) VALUES(?,?)", (key, now_iso()))


# ------------------------------------------------------------------ USERBOT SESSIYASI VA GURUHLAR

def ub_save_session(uid, phone, session):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO ub_sessions(user_id, phone, session, created) VALUES(?,?,?,?)",
                  (uid, phone, session, now_iso()))


def ub_all_sessions():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ub_sessions")]


def ub_delete_session(uid):
    with db() as c:
        c.execute("DELETE FROM ub_sessions WHERE user_id=?", (uid,))


def ub_set_group(uid, chat_id, title, on):
    with db() as c:
        if on:
            c.execute("INSERT OR REPLACE INTO ub_groups(user_id, chat_id, title) VALUES(?,?,?)",
                      (uid, chat_id, title))
        else:
            c.execute("DELETE FROM ub_groups WHERE user_id=? AND chat_id=?", (uid, chat_id))


def ub_active_groups(uid):
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM ub_groups WHERE user_id=? ORDER BY title", (uid,))]


# ------------------------------------------------------------------ YUK SO'ROVLARI VA YUK EGALARI (LEAD)

def create_request(uid, origin, destination, note, hours):
    with db() as c:
        cur = c.execute(
            "INSERT INTO cargo_requests(user_id, origin, destination, note, hours, created) VALUES(?,?,?,?,?,?)",
            (uid, origin, destination, note, hours, now_iso()))
        return cur.lastrowid


def get_request(uid, rid):
    with db() as c:
        r = c.execute("SELECT * FROM cargo_requests WHERE id=? AND user_id=?", (rid, uid)).fetchone()
        return dict(r) if r else None


def update_request(rid, **fields):
    allowed = {"status", "scanned", "found"}
    items = [(k, v) for k, v in fields.items() if k in allowed]
    if not items:
        return
    with db() as c:
        c.execute(f"UPDATE cargo_requests SET {', '.join(k + '=?' for k, _ in items)} WHERE id=?",
                  [v for _, v in items] + [rid])


def list_requests(uid, limit=5):
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM cargo_requests WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))]


def add_lead(uid, rid, m: dict, parsed: dict):
    """Topilgan yuk egasini 'new' holatida saqlaydi. Shu so'rovda allaqachon bo'lsa - None."""
    with db() as c:
        if c.execute("SELECT 1 FROM leads WHERE user_id=? AND request_id=? AND sender_id=?",
                     (uid, rid, m["sender_id"])).fetchone():
            return None
        cur = c.execute(
            """INSERT INTO leads(user_id, request_id, sender_id, access_hash, username, name, phone,
               origin, destination, cargo, ad_text, ad_link, status, created, updated)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?)""",
            (uid, rid, m["sender_id"], m.get("access_hash"), m.get("username"), m.get("name"),
             parsed.get("phone"), parsed.get("origin"), parsed.get("destination"), parsed.get("cargo"),
             m["text"][:1500], m.get("link"), now_iso(), now_iso()))
        return cur.lastrowid


def get_lead(lead_id):
    with db() as c:
        r = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(r) if r else None


def update_lead(lead_id, **fields):
    allowed = {"status", "phone", "summary", "contacted_at"}
    items = [(k, v) for k, v in fields.items() if k in allowed]
    if not items:
        return
    with db() as c:
        c.execute(f"UPDATE leads SET {', '.join(k + '=?' for k, _ in items)}, updated=? WHERE id=?",
                  [v for _, v in items] + [now_iso(), lead_id])


def merge_lead_info(lead_id, info, summary=None):
    """AI yig'gan ma'lumotlarni (narx, og'irlik...) qo'shadi. Bo'sh qiymatlar eskisini o'chirmaydi."""
    with db() as c:
        r = c.execute("SELECT info FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not r:
            return
        cur = json.loads(r["info"] or "{}")
        for k, v in (info or {}).items():
            if v not in (None, "", []):
                cur[k] = v
        c.execute("UPDATE leads SET info=?, summary=COALESCE(?, summary), updated=? WHERE id=?",
                  (json.dumps(cur, ensure_ascii=False), summary, now_iso(), lead_id))


def next_new_lead(uid, rid):
    with db() as c:
        r = c.execute("SELECT * FROM leads WHERE user_id=? AND request_id=? AND status='new' ORDER BY id LIMIT 1",
                      (uid, rid)).fetchone()
        return dict(r) if r else None


def count_leads(uid, rid, status):
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM leads WHERE user_id=? AND request_id=? AND status=?",
                         (uid, rid, status)).fetchone()[0]


def list_leads(uid, rid=None, limit=30):
    q, args = "SELECT * FROM leads WHERE user_id=?", [uid]
    if rid:
        q += " AND request_id=?"
        args.append(rid)
    with db() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit])]


def active_lead_by_sender(uid, sender_id):
    """Suhbat ketayotgan (yoki menejerga topshirilgan) yuk egasi. Boshqa lichka xabarlariga tegilmaydi."""
    with db() as c:
        r = c.execute(
            """SELECT * FROM leads WHERE user_id=? AND sender_id=?
               AND status IN ('contacted','talking','done','manual') ORDER BY id DESC LIMIT 1""",
            (uid, sender_id)).fetchone()
        return dict(r) if r else None


def recent_sender_ids(uid, days):
    """Oxirgi `days` kunda allaqachon yozilgan odamlar (qayta bezovta qilmaslik uchun)."""
    cutoff = (datetime.now(config.TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    with db() as c:
        return {r[0] for r in c.execute(
            "SELECT sender_id FROM leads WHERE user_id=? AND created>=? AND status!='new'", (uid, cutoff))}


def dms_today(uid):
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM leads WHERE user_id=? AND substr(contacted_at,1,10)=?",
                         (uid, today().isoformat())).fetchone()[0]


def add_lead_msg(lead_id, role, content):
    with db() as c:
        c.execute("INSERT INTO lead_msgs(lead_id, role, content, created) VALUES(?,?,?,?)",
                  (lead_id, role, content, now_iso()))


def lead_msgs(lead_id):
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT role, content, created FROM lead_msgs WHERE lead_id=? ORDER BY id", (lead_id,))]
