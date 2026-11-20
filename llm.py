"""Arzon AI provayderlari zanjiri (yuk qidiruvidagi ommaviy, oddiy ishlar uchun).

Tartib:
  1) Admin paneldan qo'shilgan AI lar (🛠 Admin → 🧠 AI): Groq, OpenRouter, Gemini, DeepSeek... (ustuvorlik bo'yicha)
  2) Railway Variables dagi GROQ_API_KEYS, CHEAP_AI_KEY (zaxira)
  3) Claude (EXTRACT_MODEL) - eng oxirgi zaxira, bot hech qachon to'xtamaydi.

Navbatma-navbat (ai_rotate=1): har bir so'rov keyingi AI dan boshlanadi - yuk bitta AI ga tushib qolmaydi,
bepul limitlar tekis sarflanadi. Bir AI ishlamasa - darhol keyingisi.
Kalit limiti tugasa (429) - vaqtincha chetlanadi va adminga xabar boradi.
Kalit noto'g'ri bo'lsa (401/403) - o'chiriladi va adminga xabar boradi.
Ketma-ket 3 marta tarmoq/server xatosi bersa - 10 daqiqaga chetlanadi va adminga xabar boradi.
"""
import itertools
import logging
import re
import time
from datetime import datetime, timedelta

import httpx
from anthropic import AsyncAnthropic

import config
import core
import database as dbm

_http = httpx.AsyncClient(timeout=90)
_claude = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY or "missing")
_ENV_COOLDOWN: dict = {}
_NOTIFIED: dict = {}
_FAILS: dict = {}          # provayder -> ketma-ket xatolar soni
_RR = itertools.count()    # navbatma-navbat hisoblagich
STATS = {"cheap": 0, "claude": 0, "errors": 0}

PRESETS = {
    "groq": ("Groq", "https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1/chat/completions", "google/gemma-4-31b-it:free"),
    "gemini": ("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
               "gemini-2.5-flash-lite"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/chat/completions", "deepseek-chat"),
    "openai": ("OpenAI", "https://api.openai.com/v1/chat/completions", "gpt-4.1-mini"),
}


class AIError(Exception):
    def __init__(self, kind, msg, wait=0):
        super().__init__(msg)
        self.kind, self.wait = kind, wait


def _providers():
    now = datetime.now(config.TZ)
    out = []
    for p in dbm.ai_list(active_only=True):
        cd = dbm._dt(p.get("cooldown_until"))
        if cd and cd > now:
            continue
        try:
            key = core.dec(p["api_key"])
        except Exception:
            continue
        out.append({"id": p["id"], "name": p["name"], "url": p["url"], "key": key, "model": p["model"]})
    for k in config.GROQ_API_KEYS:
        if _ENV_COOLDOWN.get(k, 0) < time.time():
            out.append({"id": None, "name": "Groq (env)", "url": config.GROQ_URL, "key": k, "model": config.GROQ_MODEL})
    if config.CHEAP_AI_KEY and _ENV_COOLDOWN.get(config.CHEAP_AI_KEY, 0) < time.time():
        out.append({"id": None, "name": "Gemini (env)", "url": config.CHEAP_AI_URL, "key": config.CHEAP_AI_KEY,
                    "model": config.CHEAP_AI_MODEL})
    if len(out) > 1 and dbm.setting("ai_rotate") != "0":
        k = next(_RR) % len(out)
        out = out[k:] + out[:k]
    return out


def _pkey(p) -> str:
    return f"id{p['id']}" if p["id"] else f"env{p['key'][-8:]}"


def cheap_enabled() -> bool:
    return bool(dbm.ai_list(active_only=True) or config.GROQ_API_KEYS or config.CHEAP_AI_KEY)


def _clean(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


async def call_openai_compat(url, key, model, system, messages, max_tokens=1500, json_mode=True) -> str:
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "system", "content": system}] + messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}", "X-Title": "Logistika Bot"}
    try:
        r = await _http.post(url, json=body, headers=headers)
    except httpx.HTTPError as e:
        raise AIError("net", f"tarmoq xatosi: {e}")
    if r.status_code == 400 and json_mode:  # ba'zi modellar json rejimini qo'llamaydi - usiz qayta urinamiz
        body.pop("response_format", None)
        r = await _http.post(url, json=body, headers=headers)
    if r.status_code == 429:
        ra = r.headers.get("retry-after")
        try:
            wait = min(6 * 3600, max(60, float(ra))) if ra else 900
        except ValueError:
            wait = 900
        raise AIError("limit", "limit tugadi (429)", wait)
    if r.status_code in (401, 403):
        raise AIError("auth", f"kalit noto'g'ri yoki bloklangan ({r.status_code})")
    if r.status_code == 404:
        raise AIError("model", f"model topilmadi: {model}")
    if r.status_code == 402:
        raise AIError("limit", "hisobda mablag' yo'q (402)", 6 * 3600)
    if r.status_code >= 400:
        raise AIError("other", f"HTTP {r.status_code}: {r.text[:150]}")
    try:
        data = r.json()
        return _clean(data["choices"][0]["message"]["content"] or "")
    except Exception:
        raise AIError("other", f"javob noto'g'ri: {r.text[:150]}")


async def _notify_once(key, text, every=3600):
    if time.time() - _NOTIFIED.get(key, 0) < every:
        return
    _NOTIFIED[key] = time.time()
    try:
        await core.notify_admins(text)
    except Exception:
        pass


async def _handle_error(p, e: AIError):
    STATS["errors"] += 1
    label = f"{p['name']} ({p['model']})" + (f" #{p['id']}" if p["id"] else "")
    logging.warning("AI xatosi %s: %s", label, e)
    if p["id"]:
        dbm.ai_bump(p["id"], False, str(e))
    if e.kind in ("net", "other"):
        n = _FAILS[_pkey(p)] = _FAILS.get(_pkey(p), 0) + 1
        if n >= 3:
            _FAILS[_pkey(p)] = 0
            e = AIError("limit", f"ketma-ket 3 marta xato: {e}", 600)
    if e.kind == "limit":
        until = time.time() + e.wait
        if p["id"]:
            dbm.ai_update(p["id"], cooldown_until=(datetime.now(config.TZ) + timedelta(seconds=e.wait)).isoformat(timespec="seconds"))
        else:
            _ENV_COOLDOWN[p["key"]] = until
        await _notify_once(f"lim:{p['key'][-8:]}", f"⚠️ AI vaqtincha ishlamayapti: {label}\nSabab: {e}\n"
                                                    f"{int(e.wait // 60)} daqiqaga chetlandi, boshqa AI ishlatilmoqda.\n"
                                                    "Yangi kalit qo'shish: 🛠 Admin panel → 🧠 AI → ➕ AI qo'shish")
    elif e.kind in ("auth", "model"):
        if p["id"]:
            dbm.ai_update(p["id"], active=0)
        else:
            _ENV_COOLDOWN[p["key"]] = time.time() + 6 * 3600
        await _notify_once(f"bad:{p['key'][-8:]}", f"❌ AI ishlamayapti va o'chirildi: {label}\nSabab: {e}\n"
                                                    "Tekshiring: 🛠 Admin panel → 🧠 AI", every=6 * 3600)


async def _claude_call(model, system, messages, max_tokens) -> str:
    resp = await _claude.messages.create(model=model, max_tokens=max_tokens, system=system, messages=messages)
    STATS["claude"] += 1
    return "".join(b.text for b in resp.content if b.type == "text")


async def _chain(system, messages, max_tokens, json_mode):
    for p in _providers():
        try:
            out = await call_openai_compat(p["url"], p["key"], p["model"], system, messages, max_tokens, json_mode)
            STATS["cheap"] += 1
            _FAILS[_pkey(p)] = 0
            if p["id"]:
                dbm.ai_bump(p["id"], True)
            return out
        except AIError as e:
            await _handle_error(p, e)
    return None


async def cheap(system: str, messages: list, max_tokens: int = 1500, json_mode: bool = True) -> str:
    """E'lonlarni tasniflash, joy nomlari, so'rovni tushunish kabi oddiy ishlar."""
    out = await _chain(system, messages, max_tokens, json_mode)
    if out is not None:
        return out
    if cheap_enabled():
        await _notify_once("all_down", "⚠️ Barcha arzon AI lar ishlamayapti yoki limiti tugagan - hozir Claude "
                                       "ishlatilmoqda (qimmatroq).\nYangi kalit qo'shing: 🛠 Admin panel → 🧠 AI")
    return await _claude_call(config.EXTRACT_MODEL, system, messages, max_tokens)


async def deal(system: str, messages: list, max_tokens: int = 900) -> str:
    """E'lon egalari bilan suhbat. DEAL_PROVIDER=cheap bo'lsa arzon zanjir, aks holda Claude (DEAL_MODEL)."""
    if config.DEAL_PROVIDER == "cheap":
        out = await _chain(system, messages, max_tokens, True)
        if out is not None:
            return out
    return await _claude_call(config.DEAL_MODEL, system, messages, max_tokens)


async def test(url, key, model):
    """-> (True, "javob vaqti") | (False, xato)"""
    t = time.time()
    try:
        out = await call_openai_compat(url, key, model, "Faqat JSON qaytaring.",
                                       [{"role": "user", "content": 'Qaytaring: {"ok": true}'}], 30, True)
        return ("ok" in out.lower()), f"{time.time() - t:.1f} s"
    except AIError as e:
        return False, str(e)
