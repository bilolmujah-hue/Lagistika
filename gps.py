"""GPS integratsiyasi: Wialon (gpsmonitor.uz) Remote API.
Token Railway Variables'dagi WIALON_TOKEN dan olinadi."""
import json
import logging
import re
import time
from datetime import datetime

import httpx

import config

_sid = None
_client = httpx.AsyncClient(timeout=20)

STALE_HOURS = 6  # shuncha soat signal kelmasa - "aloqa yo'q" deb belgilanadi


def norm(s) -> str:
    """'MAN 01 140 WLA' -> 'MAN01140WLA' (taqqoslash uchun)."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


async def _call(svc: str, params: dict, retry: bool = True):
    global _sid
    if not config.WIALON_TOKEN:
        raise RuntimeError("WIALON_TOKEN sozlanmagan")
    data = {"svc": svc, "params": json.dumps(params)}
    if svc != "token/login":
        if not _sid:
            await _login()
        data["sid"] = _sid
    r = (await _client.post(config.WIALON_URL, data=data)).json()
    if isinstance(r, dict) and r.get("error"):
        if r["error"] == 1 and retry and svc != "token/login":  # sessiya eskirgan
            _sid = None
            return await _call(svc, params, retry=False)
        raise RuntimeError(f"Wialon xatosi: {r['error']}")
    return r


async def _login():
    global _sid
    r = await _call("token/login", {"token": config.WIALON_TOKEN})
    _sid = r["eid"]
    logging.info("Wialon'ga ulandi")


async def get_units() -> list:
    """Barcha mashinalar: nom, tezlik, oxirgi signal vaqti, koordinatalar."""
    r = await _call("core/search_items", {
        "spec": {"itemsType": "avl_unit", "propName": "sys_name",
                 "propValueMask": "*", "sortType": "sys_name"},
        "force": 1, "flags": 1 | 1024, "from": 0, "to": 0})
    now = time.time()
    out = []
    for u in r.get("items", []):
        p = u.get("pos") or {}
        ts = p.get("t")
        out.append({
            "name": u.get("nm", "?"),
            "lat": p.get("y"), "lon": p.get("x"),
            "speed": p.get("s", 0),
            "ts": ts,
            "age_h": (now - ts) / 3600 if ts else None,
        })
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


def status_emoji(u) -> str:
    if u["ts"] is None or u["age_h"] is None or u["age_h"] > STALE_HOURS:
        return "⚪"      # aloqa yo'q
    return "🟢" if u["speed"] > 0 else "🟠"   # yurmoqda / to'xtab turibdi


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
