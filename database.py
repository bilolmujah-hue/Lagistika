"""Ma'lumotlar bazasi (SQLite).

Har bir menejer faqat o'z ma'lumotini ko'radi (user_id bo'yicha). Haydovchi - menejer bazasidagi
o'z yozuviga (drivers.tg_user_id) bog'langan Telegram foydalanuvchi.
Eski bazadan yangisiga o'tish (migratsiya) init_db() da avtomatik bajariladi.
"""
import json
import random
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

-- eski userbot jadvallari (faqat migratsiya uchun qoldirilgan)
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

-- ===================== YANGI JADVALLAR =====================
CREATE TABLE IF NOT EXISTS admins(user_id INTEGER PRIMARY KEY, added_by INTEGER, created TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS payments(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
    days INTEGER, status TEXT DEFAULT 'pending', method TEXT, created TEXT, expires_at TEXT,
    paid_at TEXT, txn_id INTEGER, chat_id INTEGER, msg_id INTEGER,
    receipt_file_id TEXT, receipt_kind TEXT, receipt_info TEXT, note TEXT, reviewed_by INTEGER);
CREATE INDEX IF NOT EXISTS idx_pay_user ON payments(user_id, status);

CREATE TABLE IF NOT EXISTS card_txns(
    id INTEGER PRIMARY KEY AUTOINCREMENT, msg_id INTEGER UNIQUE, is_credit INTEGER, amount REAL,
    card TEXT, merchant TEXT, title TEXT, txn_time TEXT, balance REAL, raw TEXT,
    received_at TEXT, payment_id INTEGER);

CREATE TABLE IF NOT EXISTS tg_accounts(
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, owner_id INTEGER, phone TEXT,
    session TEXT, tg_id INTEGER, username TEXT, name TEXT, active INTEGER DEFAULT 1,
    status TEXT, created TEXT);

CREATE TABLE IF NOT EXISTS pool_groups(
    id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
    title TEXT, UNIQUE(account_id, chat_id));

CREATE TABLE IF NOT EXISTS gps_requests(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, server TEXT, login TEXT,
    password TEXT, status TEXT DEFAULT 'pending', created TEXT, handled_by INTEGER, handled_at TEXT);

CREATE TABLE IF NOT EXISTS ap_campaigns(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE, text TEXT,
    interval_min INTEGER DEFAULT 5, active INTEGER DEFAULT 0, last_run TEXT, paused_until TEXT,
    note TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS ap_groups(
    id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
    title TEXT, active INTEGER DEFAULT 1, note TEXT, UNIQUE(campaign_id, chat_id));
CREATE TABLE IF NOT EXISTS ad_cache(
    key TEXT PRIMARY KEY, kind TEXT, origin TEXT, destination TEXT, cargo TEXT, phone TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS ai_providers(
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, url TEXT, api_key TEXT, model TEXT,
    active INTEGER DEFAULT 1, priority INTEGER DEFAULT 100, calls INTEGER DEFAULT 0, fails INTEGER DEFAULT 0,
    last_error TEXT, cooldown_until TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS ap_bad_chats(chat_id INTEGER PRIMARY KEY, title TEXT, reason TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS place_stems(place TEXT PRIMARY KEY, stems TEXT, created TEXT);

CREATE TABLE IF NOT EXISTS ap_log(
    campaign_id INTEGER, chat_id INTEGER, day TEXT, count INTEGER DEFAULT 0,
    PRIMARY KEY(campaign_id, chat_id, day));
"""

# Eski jadvallarga qo'shiladigan ustunlar
MIGRATIONS = {
    "users": [("username", "TEXT"), ("phone", "TEXT"), ("role", "TEXT"), ("blocked", "INTEGER DEFAULT 0"),
              ("vip", "INTEGER DEFAULT 0"), ("trial_until", "TEXT"), ("paid_until", "TEXT"),
              ("gps_url", "TEXT"), ("gps_token", "TEXT"), ("manager_id", "INTEGER"),
              ("driver_rec_id", "INTEGER"), ("company", "TEXT"), ("last_seen", "TEXT"),
              ("trial_used", "INTEGER DEFAULT 0")],
    "drivers": [("tg_user_id", "INTEGER")],
    "cargo_requests": [("mode", "TEXT DEFAULT 'cargo'"), ("parent_id", "INTEGER")],
    "leads": [("account_id", "INTEGER"), ("mode", "TEXT"), ("peer_id", "INTEGER")],
    "ap_groups": [("last_msg_id", "INTEGER"), ("del_count", "INTEGER DEFAULT 0")],
}

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_leads_acc ON leads(account_id, sender_id);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_leads_peer ON leads(account_id, peer_id);
CREATE INDEX IF NOT EXISTS idx_leads_req ON leads(user_id, request_id);
"""

STATUS_EMOJI = {
    "missing": "⚪", "noexp": "🔵", "expired": "🔴", "red": "🔴", "yellow": "🟡", "green": "🟢",
}


@contextmanager
def db():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, typ in cols:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
        c.executescript(INDEXES)
        # Eski bitta userbot sessiyasini "pool" akkauntiga o'tkazamiz (guruhlari bilan)
        if not c.execute("SELECT 1 FROM tg_accounts LIMIT 1").fetchone():
            for r in c.execute("SELECT * FROM ub_sessions").fetchall():
                cur = c.execute(
                    "INSERT INTO tg_accounts(kind, owner_id, phone, session, active, created) VALUES('pool',?,?,?,1,?)",
                    (r["user_id"], r["phone"], r["session"], now_iso()))
                aid = cur.lastrowid
                c.execute("INSERT OR IGNORE INTO pool_groups(account_id, chat_id, title) "
                          "SELECT ?, chat_id, title FROM ub_groups WHERE user_id=?", (aid, r["user_id"]))
                c.execute("UPDATE leads SET account_id=? WHERE account_id IS NULL AND user_id=?",
                          (aid, r["user_id"]))


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



def _dt(s):
    """ISO matn -> datetime (TZ bilan). Bo'sh/noto'g'ri bo'lsa None."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=config.TZ)


def fmt_dt(s, with_time=False) -> str:
    d = _dt(s) if not isinstance(s, datetime) else s
    if not d:
        return "-"
    d = d.astimezone(config.TZ)
    return d.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def phone9(s) -> str:
    """Telefonni solishtirish uchun oxirgi 9 raqam (+998 90 123 45 67 == 901234567)."""
    d = digits(s)
    return d[-9:] if len(d) >= 9 else d


# ====================================================================== FOYDALANUVCHILAR

USER_FIELDS = {"name", "username", "phone", "role", "blocked", "vip", "trial_until", "paid_until",
               "gps_url", "gps_token", "manager_id", "driver_rec_id", "company", "last_seen", "trial_used"}


def touch_user(uid, name=None, username=None) -> dict:
    """Foydalanuvchini yaratadi (bo'lmasa) va oxirgi faollikni yangilaydi."""
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id, name, created) VALUES(?,?,?)", (uid, name, now_iso()))
        c.execute("UPDATE users SET name=COALESCE(?, name), username=?, last_seen=? WHERE user_id=?",
                  (name, username, now_iso(), uid))
        return dict(c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone())


def ensure_user(uid, name=None):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id, name, created) VALUES(?,?,?)", (uid, name, now_iso()))


def get_user(uid):
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        return dict(r) if r else None


def set_user(uid, **fields):
    items = [(k, v) for k, v in fields.items() if k in USER_FIELDS]
    if not items:
        return
    ensure_user(uid)
    with db() as c:
        c.execute(f"UPDATE users SET {', '.join(k + '=?' for k, _ in items)} WHERE user_id=?",
                  [v for _, v in items] + [uid])


def all_user_ids(only_registered=False):
    q = "SELECT user_id FROM users WHERE COALESCE(blocked,0)=0"
    if only_registered:
        q += " AND role IS NOT NULL"
    with db() as c:
        return [r["user_id"] for r in c.execute(q)]


def manager_ids():
    with db() as c:
        return [r["user_id"] for r in c.execute(
            "SELECT user_id FROM users WHERE role='manager' AND COALESCE(blocked,0)=0")]


def _filter_sql(flt):
    n = now_iso()
    m = {
        "all": ("1=1", []),
        "managers": ("role='manager'", []),
        "drivers": ("role='driver'", []),
        "noreg": ("role IS NULL", []),
        "vip": ("vip=1", []),
        "blocked": ("blocked=1", []),
        "paid": ("paid_until>?", [n]),
        "trial": ("trial_until>? AND COALESCE(paid_until,'')<=? AND COALESCE(vip,0)=0", [n, n]),
        "expired": ("role='manager' AND COALESCE(vip,0)=0 AND COALESCE(paid_until,'')<=? "
                    "AND COALESCE(trial_until,'')<=?", [n, n]),
    }
    return m.get(flt, m["all"])


def count_users(flt="all"):
    w, a = _filter_sql(flt)
    with db() as c:
        return c.execute(f"SELECT COUNT(*) FROM users WHERE {w}", a).fetchone()[0]


def list_users(flt="all", offset=0, limit=10):
    w, a = _filter_sql(flt)
    with db() as c:
        return [dict(r) for r in c.execute(
            f"SELECT * FROM users WHERE {w} ORDER BY created DESC LIMIT ? OFFSET ?", a + [limit, offset])]


def user_ids_by_filter(flt="all"):
    w, a = _filter_sql(flt)
    with db() as c:
        return [r[0] for r in c.execute(f"SELECT user_id FROM users WHERE {w} AND COALESCE(blocked,0)=0", a)]


def find_users(q, limit=10):
    q = (q or "").strip()
    with db() as c:
        if q.lstrip("-").isdigit() and len(q) >= 5:
            rows = c.execute("SELECT * FROM users WHERE user_id=?", (int(q),)).fetchall()
            if rows:
                return [dict(r) for r in rows]
        if q.startswith("@"):
            return [dict(r) for r in c.execute("SELECT * FROM users WHERE lower(username)=?",
                                               (q[1:].lower(),))]
        p9 = phone9(q)
        out = []
        if len(p9) >= 7:
            out = [dict(r) for r in c.execute("SELECT * FROM users WHERE phone LIKE ?", (f"%{p9}%",))]
        if not out:
            out = [dict(r) for r in c.execute(
                "SELECT * FROM users WHERE name LIKE ? OR company LIKE ? OR username LIKE ? LIMIT ?",
                (f"%{q}%", f"%{q}%", f"%{q}%", limit))]
        return out[:limit]


def user_stats(uid):
    with db() as c:
        one = lambda q, *a: c.execute(q, a).fetchone()[0]
        return {
            "vehicles": one("SELECT COUNT(*) FROM vehicles WHERE user_id=?", uid),
            "drivers": one("SELECT COUNT(*) FROM drivers WHERE user_id=?", uid),
            "docs": one("SELECT COUNT(*) FROM documents WHERE user_id=?", uid),
            "linked_drivers": one("SELECT COUNT(*) FROM drivers WHERE user_id=? AND tg_user_id IS NOT NULL", uid),
            "payments": one("SELECT COALESCE(SUM(amount),0) FROM payments WHERE user_id=? AND status='paid'", uid),
            "searches": one("SELECT COUNT(*) FROM cargo_requests WHERE user_id=?", uid),
        }


# ====================================================================== ADMINLAR VA SOZLAMALAR

def admin_ids() -> set:
    with db() as c:
        return set(config.SUPER_ADMINS) | {r[0] for r in c.execute("SELECT user_id FROM admins")}


def is_admin(uid) -> bool:
    if uid in config.SUPER_ADMINS:
        return True
    with db() as c:
        return c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,)).fetchone() is not None


def add_admin(uid, by):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO admins(user_id, added_by, created) VALUES(?,?,?)", (uid, by, now_iso()))


def remove_admin(uid):
    with db() as c:
        return c.execute("DELETE FROM admins WHERE user_id=?", (uid,)).rowcount > 0


def list_admins():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM admins ORDER BY created")]


def settings() -> dict:
    out = dict(config.DEFAULTS)
    with db() as c:
        for r in c.execute("SELECT key, value FROM settings"):
            out[r["key"]] = r["value"]
    return out


def setting(key):
    return settings().get(key)


def setting_int(key, default=0):
    try:
        return int(float(settings().get(key) or default))
    except (TypeError, ValueError):
        return default


def set_setting(key, value):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, str(value)))


# ====================================================================== OBUNA

ACTIVE_STATES = {"admin", "vip", "paid", "trial", "driver"}


def sub_state(u):
    """(holat, tugash_vaqti). Holatlar: admin, vip, paid, trial, expired, blocked,
    driver (menejeri faol), manager_expired, unlinked, noreg."""
    if not u:
        return "noreg", None
    uid = u["user_id"]
    if u.get("blocked"):
        return "blocked", None
    if is_admin(uid):
        return "admin", None
    if u.get("vip"):
        return "vip", None
    if u.get("role") == "driver":
        mid = u.get("manager_id")
        m = get_user(mid) if mid else None
        if not m:
            return "unlinked", None
        st, until = sub_state(m)
        return ("driver", until) if st in ACTIVE_STATES else ("manager_expired", until)
    if not u.get("role"):
        return "noreg", None
    now = datetime.now(config.TZ)
    pu, tu = _dt(u.get("paid_until")), _dt(u.get("trial_until"))
    if pu and pu > now:
        return "paid", pu
    if tu and tu > now:
        return "trial", tu
    return "expired", max([x for x in (pu, tu) if x], default=None)


def has_access(u) -> bool:
    return sub_state(u)[0] in ACTIVE_STATES


def is_unlimited(uid) -> bool:
    """VIP tarif berilgan foydalanuvchi va adminlar uchun barcha limitlar olib tashlanadi."""
    if is_admin(uid):
        return True
    u = get_user(uid)
    return bool(u and u.get("vip") and not u.get("blocked"))


def start_trial(uid, days):
    u = get_user(uid)
    if u and u.get("trial_used"):
        return None
    until = (datetime.now(config.TZ) + timedelta(days=days)).isoformat(timespec="seconds")
    set_user(uid, trial_until=until, trial_used=1)
    return until


def extend_sub(uid, days) -> str:
    """Obunani `days` kunga uzaytiradi (qolgan muddat ustiga). Yangi tugash vaqtini qaytaradi."""
    u = get_user(uid) or {}
    now = datetime.now(config.TZ)
    base = now
    for k in ("paid_until", "trial_until"):
        d = _dt(u.get(k))
        if d and d > base:
            base = d
    new = (base + timedelta(days=days)).isoformat(timespec="seconds")
    set_user(uid, paid_until=new)
    return new


def end_sub(uid):
    n = now_iso()
    set_user(uid, paid_until=n, trial_until=n, trial_used=1)


# ====================================================================== HAYDOVCHINI BOG'LASH

def driver_records_by_phone(phone):
    """Shu telefon raqamli haydovchi yozuvlari (barcha menejerlar bazasidan), eng yangisi birinchi."""
    p = phone9(phone)
    if len(p) < 7:
        return []
    with db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM drivers WHERE phone IS NOT NULL AND phone<>'' ORDER BY id DESC")]
    return [r for r in rows if phone9(r["phone"]) == p]


def link_driver(user_id, manager_id, rec_id):
    with db() as c:
        c.execute("UPDATE drivers SET tg_user_id=NULL WHERE tg_user_id=?", (user_id,))
        c.execute("UPDATE drivers SET tg_user_id=? WHERE id=? AND user_id=?", (user_id, rec_id, manager_id))
    set_user(user_id, role="driver", manager_id=manager_id, driver_rec_id=rec_id)


def unlink_driver(manager_id, rec_id):
    """Haydovchini menejerdan ajratadi. Ajratilgan Telegram user_id qaytadi (yoki None)."""
    with db() as c:
        r = c.execute("SELECT tg_user_id FROM drivers WHERE id=? AND user_id=?", (rec_id, manager_id)).fetchone()
        if not r or not r["tg_user_id"]:
            return None
        tid = r["tg_user_id"]
        c.execute("UPDATE drivers SET tg_user_id=NULL WHERE id=?", (rec_id,))
        c.execute("UPDATE users SET manager_id=NULL, driver_rec_id=NULL WHERE user_id=? AND manager_id=?",
                  (tid, manager_id))
    return tid


def try_link_record(manager_id, rec_id):
    """Menejer haydovchiga telefon kiritganda: shu raqam bilan ro'yxatdan o'tgan (bog'lanmagan)
    haydovchi bo'lsa - bog'laydi. -> ('linked', uid) | ('taken', uid) | ('none', None)"""
    d = get_driver(manager_id, rec_id)
    if not d or not d.get("phone"):
        return "none", None
    p = phone9(d["phone"])
    if len(p) < 7:
        return "none", None
    with db() as c:
        users = [dict(r) for r in c.execute("SELECT * FROM users WHERE role='driver' AND phone IS NOT NULL")]
    for u in users:
        if phone9(u["phone"]) != p:
            continue
        if u.get("manager_id") and (u["manager_id"] != manager_id or u.get("driver_rec_id") != rec_id):
            return "taken", u["user_id"]
        link_driver(u["user_id"], manager_id, rec_id)
        return "linked", u["user_id"]
    return "none", None


def driver_user_of_vehicle(manager_id, vehicle_id):
    """Furaga biriktirilgan haydovchining Telegram user_id si."""
    with db() as c:
        r = c.execute("""SELECT d.tg_user_id FROM vehicles v JOIN drivers d ON d.id=v.driver_id
                         WHERE v.id=? AND v.user_id=?""", (vehicle_id, manager_id)).fetchone()
        return r["tg_user_id"] if r and r["tg_user_id"] else None


def linked_driver_users(manager_id):
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM drivers WHERE user_id=? AND tg_user_id IS NOT NULL", (manager_id,))]


# ====================================================================== TO'LOVLAR

PAY_FIELDS = {"status", "method", "paid_at", "txn_id", "chat_id", "msg_id", "receipt_file_id",
              "receipt_kind", "receipt_info", "note", "reviewed_by", "expires_at"}


def create_payment(uid, amount, days, expires_at, chat_id=None):
    with db() as c:
        cur = c.execute(
            "INSERT INTO payments(user_id, amount, days, status, created, expires_at, chat_id) VALUES(?,?,?,?,?,?,?)",
            (uid, amount, days, "pending", now_iso(), expires_at, chat_id))
        return cur.lastrowid


def get_payment(pid):
    with db() as c:
        r = c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


def update_payment(pid, **fields):
    items = [(k, v) for k, v in fields.items() if k in PAY_FIELDS]
    if not items:
        return
    with db() as c:
        c.execute(f"UPDATE payments SET {', '.join(k + '=?' for k, _ in items)} WHERE id=?",
                  [v for _, v in items] + [pid])


def open_payment(uid, hours=2):
    """Foydalanuvchining oxirgi ochiq to'lovi: kutilmoqda/tekshirilmoqda yoki yaqinda vaqti tugagan."""
    since = (datetime.now(config.TZ) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with db() as c:
        r = c.execute("""SELECT * FROM payments WHERE user_id=? AND created>=?
                         AND status IN ('pending','checking','expired','review') ORDER BY id DESC LIMIT 1""",
                      (uid, since)).fetchone()
        return dict(r) if r else None


def payments_by_status(*statuses, limit=50):
    q = f"SELECT * FROM payments WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY id DESC LIMIT ?"
    with db() as c:
        return [dict(r) for r in c.execute(q, list(statuses) + [limit])]


def amounts_in_use(hours=3) -> set:
    since = (datetime.now(config.TZ) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with db() as c:
        return {r[0] for r in c.execute(
            "SELECT amount FROM payments WHERE created>=? AND status NOT IN ('paid','cancelled','rejected')", (since,))}


def payments_matching_amount(amount, since_iso):
    with db() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM payments WHERE amount=? AND created>=?
               AND status IN ('pending','checking','expired','review') ORDER BY id DESC""", (amount, since_iso))]


def mark_paid(pid, txn_id, method, by=None) -> bool:
    with db() as c:
        cur = c.execute("""UPDATE payments SET status='paid', paid_at=?, txn_id=?, method=?, reviewed_by=?
                           WHERE id=? AND status IN ('pending','checking','expired','review')""",
                        (now_iso(), txn_id, method, by, pid))
        return cur.rowcount > 0


def list_payments(uid=None, limit=20):
    q, a = "SELECT * FROM payments WHERE status='paid'", []
    if uid:
        q += " AND user_id=?"
        a.append(uid)
    with db() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", a + [limit])]


def revenue(since_iso):
    with db() as c:
        r = c.execute("SELECT COALESCE(SUM(amount),0), COUNT(*) FROM payments WHERE status='paid' AND paid_at>=?",
                      (since_iso,)).fetchone()
        return r[0], r[1]


# --- kartaga kelgan tushumlar (CardXabar) ---

def add_card_txn(msg_id, info, raw, received_at=None):
    with db() as c:
        try:
            cur = c.execute(
                """INSERT INTO card_txns(msg_id, is_credit, amount, card, merchant, title, txn_time, balance, raw,
                   received_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (msg_id, 1 if info.get("is_credit") else 0, info.get("amount"), info.get("card"),
                 info.get("merchant"), info.get("title"), info.get("time"), info.get("balance"), raw[:2000],
                 received_at or now_iso()))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def get_txn(tid):
    with db() as c:
        r = c.execute("SELECT * FROM card_txns WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None


def free_credits(amount, since_iso, card4=None):
    """Hali hech qaysi to'lovga bog'lanmagan, shu summali tushumlar."""
    with db() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT * FROM card_txns WHERE is_credit=1 AND payment_id IS NULL AND received_at>=?
               AND ABS(amount-?)<0.01 ORDER BY id""", (since_iso, amount))]
    if card4:
        rows = [r for r in rows if not r["card"] or r["card"] == card4]
    return rows


def claim_txn(tid, pid) -> bool:
    with db() as c:
        return c.execute("UPDATE card_txns SET payment_id=? WHERE id=? AND payment_id IS NULL",
                         (pid, tid)).rowcount > 0


def release_txn(tid):
    with db() as c:
        c.execute("UPDATE card_txns SET payment_id=NULL WHERE id=?", (tid,))


def recent_txns(limit=15):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM card_txns ORDER BY id DESC LIMIT ?", (limit,))]


# ====================================================================== TELEGRAM AKKAUNTLAR (USERBOT)

ACC_FIELDS = {"phone", "session", "tg_id", "username", "name", "active", "status", "owner_id"}


def acc_add(kind, owner_id, phone, session, tg_id=None, username=None, name=None):
    with db() as c:
        cur = c.execute(
            """INSERT INTO tg_accounts(kind, owner_id, phone, session, tg_id, username, name, active, created)
               VALUES(?,?,?,?,?,?,?,1,?)""", (kind, owner_id, phone, session, tg_id, username, name, now_iso()))
        return cur.lastrowid


def acc_get(aid):
    with db() as c:
        r = c.execute("SELECT * FROM tg_accounts WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None


def acc_list(kind=None, owner_id=None, active_only=False):
    q, a = "SELECT * FROM tg_accounts WHERE 1=1", []
    if kind:
        q += " AND kind=?"
        a.append(kind)
    if owner_id is not None:
        q += " AND owner_id=?"
        a.append(owner_id)
    if active_only:
        q += " AND active=1"
    with db() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY id", a)]


def acc_update(aid, **fields):
    items = [(k, v) for k, v in fields.items() if k in ACC_FIELDS]
    if items:
        with db() as c:
            c.execute(f"UPDATE tg_accounts SET {', '.join(k + '=?' for k, _ in items)} WHERE id=?",
                      [v for _, v in items] + [aid])


def acc_delete(aid):
    with db() as c:
        c.execute("DELETE FROM tg_accounts WHERE id=?", (aid,))
        c.execute("DELETE FROM pool_groups WHERE account_id=?", (aid,))


def user_account(uid):
    rows = acc_list("user", uid)
    return rows[-1] if rows else None


def pay_account():
    rows = acc_list("pay")
    return rows[-1] if rows else None


def pool_groups(aid):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM pool_groups WHERE account_id=? ORDER BY title", (aid,))]


def pool_set_group(aid, chat_id, title, on):
    with db() as c:
        if on:
            c.execute("INSERT OR REPLACE INTO pool_groups(account_id, chat_id, title) VALUES(?,?,?)",
                      (aid, chat_id, title))
        else:
            c.execute("DELETE FROM pool_groups WHERE account_id=? AND chat_id=?", (aid, chat_id))


def pool_clear_groups(aid):
    with db() as c:
        c.execute("DELETE FROM pool_groups WHERE account_id=?", (aid,))


def pool_all_groups():
    """Faol pool akkauntlaridagi barcha tanlangan guruhlar: {chat_id: {"title", "accounts": [id..]}}"""
    with db() as c:
        rows = c.execute("""SELECT g.chat_id, g.title, g.account_id FROM pool_groups g
                            JOIN tg_accounts a ON a.id=g.account_id WHERE a.kind='pool' AND a.active=1""").fetchall()
    out = {}
    for r in rows:
        e = out.setdefault(r["chat_id"], {"title": r["title"], "accounts": []})
        e["accounts"].append(r["account_id"])
    return out


# ====================================================================== GPS SO'ROVLARI

def gps_req_add(uid, server, login, password):
    with db() as c:
        c.execute("UPDATE gps_requests SET status='replaced' WHERE user_id=? AND status='pending'", (uid,))
        return c.execute("INSERT INTO gps_requests(user_id, server, login, password, status, created) "
                         "VALUES(?,?,?,?, 'pending', ?)", (uid, server, login, password, now_iso())).lastrowid


def gps_req_get(rid):
    with db() as c:
        r = c.execute("SELECT * FROM gps_requests WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None


def gps_req_close(rid, status, by):
    with db() as c:
        c.execute("UPDATE gps_requests SET status=?, handled_by=?, handled_at=?, password=NULL WHERE id=?",
                  (status, by, now_iso(), rid))


def gps_req_pending():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM gps_requests WHERE status='pending' ORDER BY id")]


# ====================================================================== AVTO XABAR

AP_FIELDS = {"text", "interval_min", "active", "last_run", "paused_until", "note"}


def ap_get(uid):
    with db() as c:
        r = c.execute("SELECT * FROM ap_campaigns WHERE user_id=?", (uid,)).fetchone()
        return dict(r) if r else None


def ap_ensure(uid):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO ap_campaigns(user_id, interval_min, active, created) VALUES(?,5,0,?)",
                  (uid, now_iso()))
    return ap_get(uid)


def ap_update(uid, **fields):
    items = [(k, v) for k, v in fields.items() if k in AP_FIELDS]
    if items:
        ap_ensure(uid)
        with db() as c:
            c.execute(f"UPDATE ap_campaigns SET {', '.join(k + '=?' for k, _ in items)} WHERE user_id=?",
                      [v for _, v in items] + [uid])


def ap_active():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ap_campaigns WHERE active=1")]


def ap_groups(cid, active_only=False):
    q = "SELECT * FROM ap_groups WHERE campaign_id=?" + (" AND active=1" if active_only else "") + " ORDER BY title"
    with db() as c:
        return [dict(r) for r in c.execute(q, (cid,))]


def ap_set_group(cid, chat_id, title, on):
    with db() as c:
        if on:
            c.execute("INSERT OR REPLACE INTO ap_groups(campaign_id, chat_id, title, active) VALUES(?,?,?,1)",
                      (cid, chat_id, title))
        else:
            c.execute("DELETE FROM ap_groups WHERE campaign_id=? AND chat_id=?", (cid, chat_id))


def ap_clear_groups(cid):
    with db() as c:
        c.execute("DELETE FROM ap_groups WHERE campaign_id=?", (cid,))


def ap_disable_group(cid, chat_id, note):
    with db() as c:
        c.execute("UPDATE ap_groups SET active=0, note=? WHERE campaign_id=? AND chat_id=?", (note, cid, chat_id))


def ap_count(cid, chat_id, day):
    with db() as c:
        r = c.execute("SELECT count FROM ap_log WHERE campaign_id=? AND chat_id=? AND day=?",
                      (cid, chat_id, day)).fetchone()
        return r[0] if r else 0


def ap_inc(cid, chat_id, day):
    with db() as c:
        c.execute("""INSERT INTO ap_log(campaign_id, chat_id, day, count) VALUES(?,?,?,1)
                     ON CONFLICT(campaign_id, chat_id, day) DO UPDATE SET count=count+1""", (cid, chat_id, day))


def ap_sent_today(cid, day):
    with db() as c:
        return c.execute("SELECT COALESCE(SUM(count),0) FROM ap_log WHERE campaign_id=? AND day=?",
                         (cid, day)).fetchone()[0]


# ====================================================================== STATISTIKA

def global_stats():
    n = now_iso()
    t0 = datetime.now(config.TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    with db() as c:
        one = lambda q, *a: c.execute(q, a).fetchone()[0]
        out = {
            "users": one("SELECT COUNT(*) FROM users"),
            "managers": one("SELECT COUNT(*) FROM users WHERE role='manager'"),
            "drivers": one("SELECT COUNT(*) FROM users WHERE role='driver'"),
            "noreg": one("SELECT COUNT(*) FROM users WHERE role IS NULL"),
            "blocked": one("SELECT COUNT(*) FROM users WHERE blocked=1"),
            "vip": one("SELECT COUNT(*) FROM users WHERE vip=1"),
            "paid": one("SELECT COUNT(*) FROM users WHERE paid_until>?", n),
            "new_today": one("SELECT COUNT(*) FROM users WHERE created>=?", t0.isoformat(timespec="seconds")),
            "active_today": one("SELECT COUNT(*) FROM users WHERE last_seen>=?", t0.isoformat(timespec="seconds")),
            "vehicles": one("SELECT COUNT(*) FROM vehicles"),
            "drivers_db": one("SELECT COUNT(*) FROM drivers"),
            "docs": one("SELECT COUNT(*) FROM documents"),
            "searches": one("SELECT COUNT(*) FROM cargo_requests"),
            "leads": one("SELECT COUNT(*) FROM leads WHERE status NOT IN ('new','skipped')"),
            "campaigns": one("SELECT COUNT(*) FROM ap_campaigns WHERE active=1"),
            "user_accs": one("SELECT COUNT(*) FROM tg_accounts WHERE kind='user' AND active=1"),
            "gps_users": one("SELECT COUNT(*) FROM users WHERE gps_token IS NOT NULL AND gps_token<>''"),
        }
    out["trial"] = count_users("trial")
    out["expired"] = count_users("expired")
    out["rev_today"], out["pay_today"] = revenue(t0.isoformat(timespec="seconds"))
    out["rev_month"], out["pay_month"] = revenue(t0.replace(day=1).isoformat(timespec="seconds"))
    out["rev_all"], out["pay_all"] = revenue("2000-01-01")
    return out


# ====================================================================== YUK SO'ROVLARI VA YUK EGALARI (LEAD)

def create_request(uid, origin, destination, note, hours, mode="cargo", parent_id=None):
    with db() as c:
        cur = c.execute(
            "INSERT INTO cargo_requests(user_id, origin, destination, note, hours, mode, parent_id, created) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (uid, origin, destination, note, hours, mode, parent_id, now_iso()))
        return cur.lastrowid


def request_chain_senders(uid, rid) -> set:
    """«Yana qidirish» uchun: shu so'rov va undan oldingi (ota) so'rovlarda ko'rsatilgan barcha e'lon egalari."""
    reqs, cur = [], rid
    with db() as c:
        while cur and cur not in reqs and len(reqs) < 50:
            reqs.append(cur)
            r = c.execute("SELECT parent_id FROM cargo_requests WHERE id=? AND user_id=?", (cur, uid)).fetchone()
            cur = r["parent_id"] if r else None
        if not reqs:
            return set()
        return {r[0] for r in c.execute(
            f"SELECT sender_id FROM leads WHERE user_id=? AND request_id IN ({','.join('?' * len(reqs))})",
            [uid] + reqs)}


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


def add_lead(uid, rid, m: dict, parsed: dict, account_id=None, mode="cargo"):
    with db() as c:
        if c.execute("SELECT 1 FROM leads WHERE user_id=? AND request_id=? AND sender_id=?",
                     (uid, rid, m["sender_id"])).fetchone():
            return None
        cur = c.execute(
            """INSERT INTO leads(user_id, request_id, sender_id, access_hash, username, name, phone,
               origin, destination, cargo, ad_text, ad_link, status, account_id, mode, created, updated)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?,?)""",
            (uid, rid, m["sender_id"], m.get("access_hash"), m.get("username"), m.get("name"),
             parsed.get("phone"), parsed.get("origin"), parsed.get("destination"), parsed.get("cargo"),
             m["text"][:1500], m.get("link"), account_id, mode, now_iso(), now_iso()))
        return cur.lastrowid


def get_lead(lead_id):
    with db() as c:
        r = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(r) if r else None


def update_lead(lead_id, **fields):
    allowed = {"status", "phone", "summary", "contacted_at", "account_id", "access_hash", "peer_id"}
    items = [(k, v) for k, v in fields.items() if k in allowed]
    if not items:
        return
    with db() as c:
        c.execute(f"UPDATE leads SET {', '.join(k + '=?' for k, _ in items)}, updated=? WHERE id=?",
                  [v for _, v in items] + [now_iso(), lead_id])


def merge_lead_info(lead_id, info, summary=None):
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


def active_lead_by_sender(account_id, sender_id):
    with db() as c:
        r = c.execute(
            """SELECT * FROM leads WHERE account_id=? AND (sender_id=? OR peer_id=?)
               AND status IN ('contacted','talking','done','manual') ORDER BY id DESC LIMIT 1""",
            (account_id, sender_id, sender_id)).fetchone()
        return dict(r) if r else None


def recent_sender_ids(days, uid=None):
    """Oxirgi `days` kunda allaqachon yozilgan odamlar (qayta bezovta qilmaslik uchun).
    uid berilsa - faqat shu foydalanuvchi nomidan yozilganlar (har kim o'z akkauntidan yozadi)."""
    cutoff = (datetime.now(config.TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    q, a = "SELECT sender_id FROM leads WHERE contacted_at>=?", [cutoff]
    if uid is not None:
        q += " AND user_id=?"
        a.append(uid)
    with db() as c:
        return {r[0] for r in c.execute(q, a)}


def dms_today_account(account_id):
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM leads WHERE account_id=? AND substr(contacted_at,1,10)=?",
                         (account_id, today().isoformat())).fetchone()[0]


def dms_today_user(uid):
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


def clear_history(uid):
    with db() as c:
        c.execute("DELETE FROM messages WHERE user_id=?", (uid,))


# ====================================================================== E'LON TAHLILI KESHI (AI xarajatini kamaytirish)

def ad_cache_get(keys) -> dict:
    keys = list(keys)
    out = {}
    with db() as c:
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            for r in c.execute(f"SELECT * FROM ad_cache WHERE key IN ({','.join('?' * len(part))})", part):
                out[r["key"]] = dict(r)
    return out


def ad_cache_put(rows):
    with db() as c:
        c.executemany("INSERT OR REPLACE INTO ad_cache(key, kind, origin, destination, cargo, phone, created) "
                      "VALUES(?,?,?,?,?,?,?)",
                      [(r["key"], r.get("kind"), r.get("origin"), r.get("destination"), r.get("cargo"),
                        r.get("phone"), now_iso()) for r in rows])


def ad_cache_cleanup(days=10):
    cutoff = (datetime.now(config.TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    with db() as c:
        c.execute("DELETE FROM ad_cache WHERE created<?", (cutoff,))


def place_stems_get(place):
    with db() as c:
        r = c.execute("SELECT stems FROM place_stems WHERE place=?", (place,)).fetchone()
        return json.loads(r["stems"]) if r else None


def place_stems_put(place, stems):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO place_stems(place, stems, created) VALUES(?,?,?)",
                  (place, json.dumps(stems, ensure_ascii=False), now_iso()))


def ap_group_set(cid, chat_id, **fields):
    allowed = {"last_msg_id", "del_count", "active", "note"}
    items = [(k, v) for k, v in fields.items() if k in allowed]
    if items:
        with db() as c:
            c.execute(f"UPDATE ap_groups SET {', '.join(k + '=?' for k, _ in items)} WHERE campaign_id=? AND chat_id=?",
                      [v for _, v in items] + [cid, chat_id])


def ap_group_get(cid, chat_id):
    with db() as c:
        r = c.execute("SELECT * FROM ap_groups WHERE campaign_id=? AND chat_id=?", (cid, chat_id)).fetchone()
        return dict(r) if r else None


def ap_bad_add(chat_id, title, reason):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO ap_bad_chats(chat_id, title, reason, created) VALUES(?,?,?,?)",
                  (chat_id, title, reason, now_iso()))


def ap_bad_ids() -> dict:
    with db() as c:
        return {r["chat_id"]: r["reason"] for r in c.execute("SELECT chat_id, reason FROM ap_bad_chats")}


# ====================================================================== AI PROVAYDERLAR (admin paneldan qo'shiladi)

AI_FIELDS = {"name", "url", "api_key", "model", "active", "priority", "calls", "fails", "last_error", "cooldown_until"}


def ai_add(name, url, api_key, model):
    with db() as c:
        pr = (c.execute("SELECT COALESCE(MAX(priority),0) FROM ai_providers").fetchone()[0] or 0) + 10
        return c.execute("INSERT INTO ai_providers(name, url, api_key, model, active, priority, created) "
                         "VALUES(?,?,?,?,1,?,?)", (name, url, api_key, model, pr, now_iso())).lastrowid


def ai_list(active_only=False):
    q = "SELECT * FROM ai_providers" + (" WHERE active=1" if active_only else "") + " ORDER BY priority, id"
    with db() as c:
        return [dict(r) for r in c.execute(q)]


def ai_get(pid):
    with db() as c:
        r = c.execute("SELECT * FROM ai_providers WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


def ai_update(pid, **fields):
    items = [(k, v) for k, v in fields.items() if k in AI_FIELDS]
    if items:
        with db() as c:
            c.execute(f"UPDATE ai_providers SET {', '.join(k + '=?' for k, _ in items)} WHERE id=?",
                      [v for _, v in items] + [pid])


def ai_bump(pid, ok, err=None):
    with db() as c:
        if ok:
            c.execute("UPDATE ai_providers SET calls=calls+1, last_error=NULL WHERE id=?", (pid,))
        else:
            c.execute("UPDATE ai_providers SET fails=fails+1, last_error=? WHERE id=?", ((err or "")[:300], pid))


def ai_delete(pid):
    with db() as c:
        c.execute("DELETE FROM ai_providers WHERE id=?", (pid,))
