"""AI provayderlari.

Ish taqsimoti:
  • Claude (PLAN_MODEL) - "aqlli" ishlar: yo'nalish rejasi (joy nomining barcha tillardagi yozilishi, hududi),
    arzon AI javobi buzuq bo'lsa - shu paketni qayta tahlil qilish. Natijalar keshlanadi -> xarajat juda kam.
  • Arzon AI lar (🛠 Admin → 🧠 AI): DeepSeek, OpenAI, OpenRouter, Gemini yoki istalgan OpenAI-mos API -
    ommaviy ishlar (e'lonlarni saralash). Claude tuzgan aniq ko'rsatma (prompt) bilan ishlaydi.

Navbatma-navbat (ai_rotate=1): har bir so'rov keyingi AI dan boshlanadi - yuk bitta AI ga tushib qolmaydi.
Bir AI ishlamasa - darhol keyingisi, hammasi ishlamasa - Claude. Bot hech qachon to'xtamaydi.
Kalit limiti tugasa (429/402) - vaqtincha chetlanadi va adminga xabar boradi.
Kalit noto'g'ri bo'lsa (401/403) yoki model yo'q (404) - o'chiriladi va adminga xabar boradi.
Ketma-ket 3 marta tarmoq/server xatosi bersa - 10 daqiqaga chetlanadi va adminga xabar boradi.
Model parametrlarni qabul qilmasa (yangi OpenAI: max_completion_tokens, temperature yo'q; DeepSeek reasoner:
response_format yo'q) - bot buni o'zi aniqlaydi va eslab qoladi.
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
_QUIRKS: dict = {}         # (url, model) -> {"tokens_key", "no_temp", "no_json"}
_RR = itertools.count()    # navbatma-navbat hisoblagich
_PURGED = False
STATS = {"cheap": 0, "claude": 0, "errors": 0}

PRESETS = {
    "deepseek": ("DeepSeek", "https://api.deepseek.com/chat/completions", "deepseek-chat"),
    "openai": ("OpenAI", "https://api.openai.com/v1/chat/completions", "gpt-4.1-mini"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1/chat/completions", "google/gemma-4-31b-it:free"),
    "gemini": ("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
               "gemini-2.5-flash-lite"),
}

_BANNED = ("groq",)  # botdan butunlay olib tashlangan xizmatlar


class AIError(Exception):
    def __init__(self, kind, msg, wait=0):
        super().__init__(msg)
        self.kind, self.wait = kind, wait


def _banned(url) -> bool:
    u = (url or "").lower()
    return any(b in u for b in _BANNED)


def purge_banned():
    """Bazada qolgan Groq yozuvlarini bir marta o'chiradi."""
    global _PURGED
    if _PURGED:
        return
    try:
        for p in dbm.ai_list():
            if _banned(p["url"]) or _banned(p["name"]):
                dbm.ai_delete(p["id"])
                logging.info("Groq provayderi o'chirildi: #%s", p["id"])
        _PURGED = True
    except Exception:
        logging.warning("Groq yozuvlarini o'chirib bo'lmadi", exc_info=True)


def _providers():
    purge_banned()
    now = datetime.now(config.TZ)
    out = []
    for p in dbm.ai_list(active_only=True):
        if _banned(p["url"]):
            continue
        cd = dbm._dt(p.get("cooldown_until"))
        if cd and cd > now:
            continue
        try:
            key = core.dec(p["api_key"])
        except Exception:
            continue
        out.append({"id": p["id"], "name": p["name"], "url": p["url"], "key": key, "model": p["model"]})
    if (config.CHEAP_AI_KEY and not _banned(config.CHEAP_AI_URL)
            and _ENV_COOLDOWN.get(config.CHEAP_AI_KEY, 0) < time.time()):
        out.append({"id": None, "name": "Zaxira (env)", "url": config.CHEAP_AI_URL, "key": config.CHEAP_AI_KEY,
                    "model": config.CHEAP_AI_MODEL})
    if len(out) > 1 and dbm.setting("ai_rotate") != "0":
        k = next(_RR) % len(out)
        out = out[k:] + out[:k]
    return out


def _pkey(p) -> str:
    return f"id{p['id']}" if p["id"] else f"env{p['key'][-8:]}"


def cheap_enabled() -> bool:
    return bool([p for p in dbm.ai_list(active_only=True) if not _banned(p["url"])] or config.CHEAP_AI_KEY)


def _clean(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


async def call_openai_compat(url, key, model, system, messages, max_tokens=1500, json_mode=True) -> str:
    q = _QUIRKS.setdefault((url, model), {})
    headers = {"Authorization": f"Bearer {key}", "X-Title": "Logistika Bot"}
    r = None
    for _ in range(4):  # parametr xatolarini o'zi tuzatib qayta urinadi
        body = {"model": model, q.get("tokens_key", "max_tokens"): max_tokens,
                "messages": [{"role": "system", "content": system}] + messages}
        if not q.get("no_temp"):
            body["temperature"] = 0
        if json_mode and not q.get("no_json"):
            body["response_format"] = {"type": "json_object"}
        try:
            r = await _http.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise AIError("net", f"tarmoq xatosi: {e}")
        if r.status_code != 400:
            break
        err = r.text.lower()
        if "max_tokens" in err and q.get("tokens_key") != "max_completion_tokens":
            q["tokens_key"] = "max_completion_tokens"
        elif "temperature" in err and not q.get("no_temp"):
            q["no_temp"] = True
        elif ("response_format" in err or "json" in err) and not q.get("no_json"):
            q["no_json"] = True
        else:
            break
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
    """E'lonlarni saralash, so'rovni tushunish kabi ommaviy ishlar (DeepSeek, OpenAI...)."""
    out = await _chain(system, messages, max_tokens, json_mode)
    if out is not None:
        return out
    if cheap_enabled():
        await _notify_once("all_down", "⚠️ Barcha arzon AI lar ishlamayapti yoki limiti tugagan - hozir Claude "
                                       "ishlatilmoqda (qimmatroq).\nYangi kalit qo'shing: 🛠 Admin panel → 🧠 AI")
    return await _claude_call(config.EXTRACT_MODEL, system, messages, max_tokens)


async def smart(system: str, messages: list, max_tokens: int = 1500) -> str:
    """Aniqlik muhim ishlar - Claude. Claude ishlamasa - arzon AI zanjiri."""
    try:
        return await _claude_call(config.PLAN_MODEL, system, messages, max_tokens)
    except Exception as e:
        STATS["errors"] += 1
        logging.warning("Claude xatosi (smart): %s", e)
        await _notify_once("claude_down", f"⚠️ Claude ishlamayapti: {str(e)[:200]}\nVaqtincha arzon AI ishlatilmoqda. "
                                          "ANTHROPIC_API_KEY va balansni tekshiring.", every=3 * 3600)
        out = await _chain(system, messages, max_tokens, True)
        if out is None:
            raise
        return out


async def deal(system: str, messages: list, max_tokens: int = 900) -> str:
    """E'lon egalari bilan suhbat. DEAL_PROVIDER=cheap -> avval arzon AI, aks holda avval Claude.
    Biri ishlamasa - ikkinchisi (suhbat hech qachon to'xtab qolmaydi)."""
    if config.DEAL_PROVIDER == "cheap":
        out = await _chain(system, messages, max_tokens, True)
        if out is not None:
            return out
        return await _claude_call(config.DEAL_MODEL, system, messages, max_tokens)
    try:
        return await _claude_call(config.DEAL_MODEL, system, messages, max_tokens)
    except Exception as e:
        STATS["errors"] += 1
        logging.warning("Claude xatosi (suhbat): %s", e)
        await _notify_once("claude_deal", f"⚠️ Claude (mijozlar bilan suhbat) ishlamayapti: {str(e)[:200]}\n"
                                          "Vaqtincha arzon AI ishlatilmoqda.", every=3 * 3600)
        out = await _chain(system, messages, max_tokens, True)
        if out is None:
            raise
        return out


async def test(url, key, model):
    """-> (True, "javob vaqti") | (False, xato)"""
    if _banned(url):
        return False, "Bu xizmat botda ishlatilmaydi"
    t = time.time()
    try:
        out = await call_openai_compat(url, key, model, "Faqat JSON qaytaring.",
                                       [{"role": "user", "content": 'Qaytaring JSON: {"ok": true}'}], 400, True)
        return ("ok" in out.lower()), f"{time.time() - t:.1f} s"
    except AIError as e:
        return False, str(e)
