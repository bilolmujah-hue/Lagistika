"""GPS integratsiyasi: Wialon (gpsmonitor.uz) Remote API.
Har bir foydalanuvchining o'z tokeni (bazada shifrlangan) - admin GPS so'rovini ko'rib kiritadi.

Imkoniyatlar: hozirgi joylashuv, tezlik, manzil, kunlik/oylik yurgan masofa (km),
yurish vaqti, to'xtashlar, butun park hisoboti."""
import asyncio
import json
import logging
import math
import re
import time
from datetime import date, datetime, timedelta

import httpx

import config

STALE_HOURS = 6        # shuncha soat signal kelmasa - "aloqa yo'q"
UNITS_CACHE_SEC = 20   # mashinalar ro'yxatini shuncha soniya keshda saqlaymiz
MOVE_KMH = 3           # shundan past tezlik = to'xtagan (GPS shovqini)
MAX_JUMP_KMH = 180     # ikki nuqta orasida bundan tez "ko'chish" - GPS xatosi, hisobga olinmaydi
MIN_STOP_MIN = 15      # shundan qisqa to'xtash ko'rsatilmaydi
MAX_DAYS = 31

_http = httpx.AsyncClient(timeout=60)
_CLIENTS: dict = {}


def norm(s) -> str:
    """'MAN 01 140 WLA' -> 'MAN01140WLA' (taqqoslash uchun)."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


# ====================================================================== WIALON API

def ajax_url(server: str) -> str:
    """'1.gpsmonitor.uz' / 'https://1.gpsmonitor.uz' -> 'https://1.gpsmonitor.uz/wialon/ajax.html'"""
    s = (server or "").strip().rstrip("/")
    if not s:
        return config.WIALON_URL
    if not s.startswith("http"):
        s = "https://" + s
    if "ajax.html" in s:
        return s
    return s + "/wialon/ajax.html"


class Wialon:
    """Bitta foydalanuvchining Wialon sessiyasi (har bir token uchun alohida)."""

    def __init__(self, url: str, token: str):
        self.url, self.token = url, token
        self.sid = None
        self.lock = asyncio.Lock()
        self.cache = {"t": 0.0, "data": []}

    async def call(self, svc: str, params: dict, retry: bool = True):
        data = {"svc": svc, "params": json.dumps(params)}
        if svc not in ("token/login", "core/login"):
            await self.ensure()
            data["sid"] = self.sid
        r = (await _http.post(self.url, data=data)).json()
        if isinstance(r, dict) and r.get("error"):
            if r["error"] == 1 and retry and svc not in ("token/login", "core/login"):  # sessiya eskirgan
                self.sid = None
                return await self.call(svc, params, retry=False)
            raise RuntimeError(f"Wialon xatosi: {r['error']}")
        return r

    async def ensure(self):
        if self.sid:
            return
        async with self.lock:
            if self.sid:
                return
            if self.token.startswith("pwd:"):  # token o'rniga login/parol (eski Wialon Local serverlar)
                cr = json.loads(self.token[4:])
                r = await self.call("core/login", {"user": cr["u"], "password": cr["p"]})
            else:
                r = await self.call("token/login", {"token": self.token})
            self.sid = r["eid"]


def get_client(url: str, token: str) -> Wialon:
    key = (url, token)
    w = _CLIENTS.get(key)
    if not w:
        w = _CLIENTS[key] = Wialon(url, token)
    return w


def client_for_user(u):
    """Foydalanuvchining GPS ulanishi. Haydovchi uchun - menejerining GPS i. Yo'q bo'lsa None."""
    import core
    import database as dbm
    if not u:
        return None
    if u.get("role") == "driver" and u.get("manager_id"):
        u = dbm.get_user(u["manager_id"]) or {}
    tok = core.dec(u.get("gps_token")) if u.get("gps_token") else None
    if tok:
        return get_client(ajax_url(u.get("gps_url") or config.GPS_DEFAULT_SERVER), tok)
    if config.WIALON_TOKEN and u.get("user_id") in config.SUPER_ADMINS:
        return get_client(config.WIALON_URL, config.WIALON_TOKEN)
    return None


def site_root(server: str) -> str:
    s = (server or config.GPS_DEFAULT_SERVER).strip().rstrip("/")
    if not s.startswith("http"):
        s = "https://" + s
    return s.split("/wialon/")[0]


def extract_token(text: str) -> str:
    """Admin tokenni o'zini yoki brauzerdagi butun manzilni (…access_token=XXX…) yuborsa ham ajratib oladi."""
    m = re.search(r"access_token=([0-9A-Za-z]{40,100})", text or "")
    if m:
        return m.group(1)
    parts = (text or "").split()
    return parts[-1] if parts else ""


def oauth_link(server: str) -> str:
    """Admin shu havolani ochib, foydalanuvchi login/paroli bilan kirsa - manzil satrida access_token chiqadi."""
    root = site_root(server)
    return (f"{root}/login.html?client_id=LogistikaBot&access_type=-1&activation_time=0&duration=0"
            f"&flags=0x1&redirect_uri={root}/post_token.html")


async def check_token(server: str, token: str):
    """Tokenni tekshiradi. -> (True, akkaunt_nomi) | (False, xato_matni)"""
    try:
        w = Wialon(ajax_url(server), token)
        await w.ensure()
        r = await w.call("core/search_items", {
            "spec": {"itemsType": "avl_unit", "propName": "sys_name", "propValueMask": "*", "sortType": "sys_name"},
            "force": 1, "flags": 1, "from": 0, "to": 0})
        return True, f"{len(r.get('items', []))} ta mashina"
    except Exception as e:
        return False, str(e)


async def auto_connect(server: str, login: str, password: str):
    """Login/parol bilan GPS ni avtomatik ulashga urinadi.
    1) Wialon rasmiy login sahifasi (login.html -> oauth.html) orqali doimiy token olish;
    2) bo'lmasa - eski core/login (ba'zi Wialon Local serverlarda ishlaydi).
    -> (saqlanadigan_kalit, izoh) yoki (None, xato)"""
    root = site_root(server)
    params = {"client_id": "LogistikaBot", "access_type": "-1", "activation_time": "0", "duration": "0",
              "flags": "0x1", "response_type": "token", "redirect_uri": f"{root}/post_token.html"}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as c:
            r = await c.get(f"{root}/login.html", params=params)
            sign = None
            for pat in (r'name="sign"[^>]*value="([^"]+)"', r'value="([^"]+)"[^>]*name="sign"', r'"sign"\s*:\s*"([^"]+)"'):
                m = re.search(pat, r.text)
                if m:
                    sign = m.group(1)
                    break
            data = dict(params, login=login, passw=password)
            if sign:
                data["sign"] = sign
            r2 = await c.post(f"{root}/oauth.html", data=data)
            token = extract_token(r2.headers.get("location", "") + " " + r2.text)
            if token and len(token) >= 40 and re.fullmatch(r"[0-9A-Za-z]+", token):
                ok, info = await check_token(root, token)
                if ok:
                    return token, info
    except Exception as e:
        logging.info("GPS OAuth orqali token olinmadi: %s", e)
    cred = "pwd:" + json.dumps({"u": login, "p": password})
    ok, info = await check_token(root, cred)
    if ok:
        return cred, info
    return None, info


async def get_units(w: Wialon) -> list:
    """Barcha mashinalar: id, nom, tezlik, oxirgi signal vaqti, koordinatalar, qurilma parametrlari."""
    if time.time() - w.cache["t"] < UNITS_CACHE_SEC and w.cache["data"]:
        return w.cache["data"]
    r = await w.call("core/search_items", {
        "spec": {"itemsType": "avl_unit", "propName": "sys_name",
                 "propValueMask": "*", "sortType": "sys_name"},
        "force": 1, "flags": 1 | 0x20 | 0x400, "from": 0, "to": 0})
    now = time.time()
    out = []
    for u in r.get("items", []):
        p = u.get("pos") or {}
        ts = p.get("t")
        raw = ((u.get("lmsg") or {}).get("p")) or {}
        params = {k: v for k, v in raw.items() if isinstance(v, (int, float, str))}
        out.append({
            "id": u.get("id"),
            "name": u.get("nm", "?"),
            "lat": p.get("y"), "lon": p.get("x"),
            "speed": p.get("s", 0),
            "ts": ts,
            "age_h": (now - ts) / 3600 if ts else None,
            "params": dict(list(params.items())[:30]),
        })
    w.cache.update(t=time.time(), data=out)
    return out


def find_unit(units: list, plate: str):
    """Fura raqami (01140WLA) bo'yicha Wialon'dagi mashinani topadi."""
    p = norm(plate)
    if not p:
        return None
    for u in units:
        n = norm(u["name"])
        if p in n or (len(n) >= 6 and n in p):
            return u
    return None


# ====================================================================== HOZIRGI HOLAT

def status_emoji(u) -> str:
    if u["ts"] is None or u["age_h"] is None or u["age_h"] > STALE_HOURS:
        return "⚪"      # aloqa yo'q
    return "🟢" if u["speed"] > 0 else "🟠"   # yurmoqda / to'xtab turibdi


def status_word(u) -> str:
    return {"🟢": "yurmoqda", "🟠": "to'xtab turibdi", "⚪": "aloqa yo'q"}[status_emoji(u)]


def maps_link(u) -> str:
    return f"https://maps.google.com/?q={u['lat']},{u['lon']}"


def _ago(u) -> str:
    if u["ts"] is None:
        return "ma'lumot yo'q"
    t = datetime.fromtimestamp(u["ts"], config.TZ).strftime("%d.%m %H:%M")
    h = u["age_h"]
    if h < 1:
        return f"{t} ({int(h * 60)} daq oldin)"
    if h < 48:
        return f"{t} ({int(h)} soat oldin)"
    return f"{t} ({int(h // 24)} kun oldin)"


def unit_text(u) -> str:
    lines = [f"{status_emoji(u)} {u['name']}"]
    if u["lat"] is None:
        lines.append("Joylashuv ma'lumoti yo'q")
        return "\n".join(lines)
    lines.append(f"⚡ Tezlik: {u['speed']} km/soat")
    lines.append(f"🕒 Oxirgi signal: {_ago(u)}")
    lines.append(f"📍 {maps_link(u)}")
    return "\n".join(lines)


def fleet_text(units: list) -> str:
    moving = sum(1 for u in units if status_emoji(u) == "🟢")
    stopped = sum(1 for u in units if status_emoji(u) == "🟠")
    offline = sum(1 for u in units if status_emoji(u) == "⚪")
    head = (f"📍 GPS holati ({len(units)} ta mashina)\n"
            f"🟢 yurmoqda: {moving} · 🟠 to'xtagan: {stopped} · ⚪ aloqa yo'q: {offline}\n")
    return head + "\n" + "\n\n".join(unit_text(u) for u in units)


async def reverse_geocode(lat, lon):
    """Koordinata -> manzil (OpenStreetMap). Xato bo'lsa None qaytaradi."""
    if lat is None or lon is None:
        return None
    try:
        r = await _http.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 16,
                    "accept-language": "ru,uz,en"},
            headers={"User-Agent": "logistika-bot/1.0"}, timeout=8)
        return (r.json() or {}).get("display_name")
    except Exception:
        logging.warning("Manzilni aniqlab bo'lmadi", exc_info=True)
        return None


async def unit_summary(u, with_address: bool = True) -> dict:
    """AI uchun: mashinaning hozirgi holati (lug'at)."""
    out = {"gps_nomi": u["name"], "holat": status_word(u)}
    if u["lat"] is None:
        out["joylashuv"] = "ma'lumot yo'q"
        return out
    out.update({
        "tezlik_km_soat": u["speed"],
        "oxirgi_signal": _ago(u),
        "koordinata": f"{u['lat']:.5f}, {u['lon']:.5f}",
        "xarita": maps_link(u),
    })
    if with_address:
        addr = await reverse_geocode(u["lat"], u["lon"])
        if addr:
            out["manzil"] = addr
    if u.get("params"):
        out["qurilma_parametrlari"] = u["params"]
    return out


# ====================================================================== YURGAN MASOFA (KM)

def _hav(lat1, lon1, lat2, lon2) -> float:
    """Ikki nuqta orasidagi masofa (km)."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def day_bounds(d: date):
    """Kunning (boshi, oxiri) unix vaqtlari. Bugun bo'lsa - hozirgacha."""
    start = datetime(d.year, d.month, d.day, tzinfo=config.TZ)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return int(start.timestamp()), min(int(end.timestamp()), int(time.time()))


def _hm(ts) -> str:
    return datetime.fromtimestamp(ts, config.TZ).strftime("%H:%M")


async def load_points(w: Wialon, unit_id, t1: int, t2: int) -> list:
    """Vaqt oralig'idagi GPS nuqtalari: [(vaqt, lat, lon, tezlik), ...]."""
    if t2 <= t1:
        return []
    try:
        r = await w.call("messages/load_interval", {
            "itemId": unit_id, "timeFrom": t1, "timeTo": t2,
            "flags": 0, "flagsMask": 65280, "loadCount": 4294967295})
        pts = []
        for m in r.get("messages", []):
            p = m.get("pos")
            if p and p.get("y") is not None and p.get("x") is not None:
                pts.append((m["t"], p["y"], p["x"], p.get("s") or 0))
        pts.sort(key=lambda x: x[0])
        return pts
    finally:
        try:  # Wialon serverida xotirani bo'shatish
            await w.call("messages/unload", {}, retry=False)
        except Exception:
            pass


def analyze(pts: list) -> dict:
    """Nuqtalardan: masofa, yurish vaqti, eng yuqori tezlik, harakat boshlanishi/tugashi, to'xtashlar."""
    km, moving_s, vmax = 0.0, 0, 0
    first_move = last_move = None
    stops, stop, prev = [], None, None
    for t, lat, lon, s in pts:
        if s <= 150:
            vmax = max(vmax, s)
        if prev:
            pt, plat, plon, ps = prev
            dt = t - pt
            if dt > 0:
                d = _hav(plat, plon, lat, lon)
                implied = d / (dt / 3600)
                moved = s >= MOVE_KMH or ps >= MOVE_KMH or (dt > 600 and d > 1.0)
                if moved and implied <= MAX_JUMP_KMH:
                    km += d
                if ps >= MOVE_KMH and dt <= 900:
                    moving_s += dt
        if s >= MOVE_KMH:
            first_move = first_move or t
            last_move = t
            if stop:
                if t - stop[0] >= MIN_STOP_MIN * 60:
                    stops.append((stop[0], t, stop[1], stop[2]))
                stop = None
        elif stop is None:
            stop = (t, lat, lon)
        prev = (t, lat, lon, s)
    if stop and prev and prev[0] - stop[0] >= MIN_STOP_MIN * 60:
        stops.append((stop[0], prev[0], stop[1], stop[2]))
    return {"km": km, "moving_s": moving_s, "vmax": vmax, "first": first_move, "last": last_move,
            "stops": stops, "n": len(pts)}


def _day_dict(d: date, a: dict, detail: bool) -> dict:
    out = {"sana": d.strftime("%d.%m.%Y")}
    if a["n"] == 0:
        out.update({"masofa_km": 0, "izoh": "Shu kunda GPS ma'lumoti yo'q"})
        return out
    h, m = divmod(a["moving_s"] // 60, 60)
    out.update({
        "masofa_km": round(a["km"], 1),
        "yurgan_vaqti": f"{h} soat {m} daq",
        "eng_yuqori_tezlik_km_soat": a["vmax"],
        "harakat_boshlandi": _hm(a["first"]) if a["first"] else None,
        "oxirgi_harakat": _hm(a["last"]) if a["last"] else None,
        "toxtashlar_soni": len(a["stops"]),
    })
    if detail and a["stops"]:
        out["toxtashlar"] = [{
            "vaqt": f"{_hm(s)}-{_hm(e)}", "davomiyligi_daq": (e - s) // 60,
            "joy": f"https://maps.google.com/?q={lat},{lon}"} for s, e, lat, lon in a["stops"][:12]]
    return out


async def day_report(w: Wialon, unit: dict, d: date, detail: bool = False) -> dict:
    t1, t2 = day_bounds(d)
    return _day_dict(d, analyze(await load_points(w, unit["id"], t1, t2)), detail)


async def period_report(w: Wialon, unit: dict, d1: date, d2: date, detail: bool = False) -> dict:
    """d1..d2 oralig'ida har kun uchun hisobot (eng ko'pi MAX_DAYS kun)."""
    if d2 < d1:
        d1, d2 = d2, d1
    if (d2 - d1).days + 1 > MAX_DAYS:
        raise ValueError(f"Eng ko'pi {MAX_DAYS} kunlik oraliq so'rash mumkin")
    days = [d1 + timedelta(days=i) for i in range((d2 - d1).days + 1)]
    sem = asyncio.Semaphore(3)

    async def one(d):
        async with sem:
            return await day_report(w, unit, d, detail=detail and len(days) == 1)

    res = await asyncio.gather(*(one(d) for d in days))
    return {"fura": unit["name"], "kunlar": res,
            "jami_km": round(sum(r["masofa_km"] for r in res), 1)}


async def fleet_mileage(w: Wialon, units: list, d: date) -> dict:
    """Butun park: shu kuni har bir mashina necha km yurgan."""
    sem = asyncio.Semaphore(4)

    async def one(u):
        async with sem:
            try:
                r = await day_report(w, u, d)
                return {"fura": u["name"], "km": r["masofa_km"], "yurgan_vaqti": r.get("yurgan_vaqti")}
            except Exception as e:  # bitta mashina xatosi hammasini buzmasin
                logging.warning("Km hisoblab bo'lmadi: %s (%s)", u["name"], e)
                return {"fura": u["name"], "km": None, "xato": "hisoblab bo'lmadi"}

    rows = await asyncio.gather(*(one(u) for u in units if u.get("id")))
    rows.sort(key=lambda r: (r["km"] is None, -(r["km"] or 0)))
    return {"sana": d.strftime("%d.%m.%Y"), "mashinalar": rows,
            "jami_km": round(sum(r["km"] or 0 for r in rows), 1)}
