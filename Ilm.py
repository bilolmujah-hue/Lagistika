"""Arzon AI provayderlari zanjiri (yuk qidiruvidagi ommaviy, oddiy ishlar uchun).

Tartib: Groq -> Gemini (yoki boshqa OpenAI-mos API) -> Claude.
Birinchisi ishlamasa yoki limiti tugasa (429), avtomatik keyingisiga o'tadi - bot to'xtamaydi.

Railway Variables:
  GROQ_API_KEYS=gsk_aaa,gsk_bbb      (bir nechta kalit vergul bilan; limiti tugagani vaqtincha chetlanadi)
  GROQ_MODEL=llama-3.3-70b-versatile
  CHEAP_AI_KEY=AIza...                (Gemini)  CHEAP_AI_MODEL=gemini-2.5-flash-lite
Hech biri bo'lmasa - Claude (EXTRACT_MODEL).
"""
import logging
import time

import httpx
from anthropic import AsyncAnthropic

import config

_http = httpx.AsyncClient(timeout=90)
_claude = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY or "missing")
_COOLDOWN: dict = {}   # kalit -> shu vaqtgacha ishlatilmaydi (limit tugagan)
_RR = {"i": 0}

STATS = {"groq": 0, "gemini": 0, "claude": 0, "errors": 0}


def _providers():
    out = []
    keys = [k for k in config.GROQ_API_KEYS if _COOLDOWN.get(k, 0) < time.time()]
    if keys:
        _RR["i"] = (_RR["i"] + 1) % len(keys)
        keys = keys[_RR["i"]:] + keys[:_RR["i"]]
        out += [("groq", config.GROQ_URL, k, config.GROQ_MODEL) for k in keys]
    if config.CHEAP_AI_KEY and _COOLDOWN.get(config.CHEAP_AI_KEY, 0) < time.time():
        out.append(("gemini", config.CHEAP_AI_URL, config.CHEAP_AI_KEY, config.CHEAP_AI_MODEL))
    return out


def cheap_enabled() -> bool:
    return bool(config.GROQ_API_KEYS or config.CHEAP_AI_KEY)


async def _openai_compat(url, key, model, system, messages, max_tokens, json_mode) -> str:
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "system", "content": system}] + messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = await _http.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
    if r.status_code == 429:
        retry = r.headers.get("retry-after")
        try:
            wait = min(3600, max(30, float(retry))) if retry else 600
        except ValueError:
            wait = 600
        _COOLDOWN[key] = time.time() + wait
        raise RuntimeError(f"limit (429), {int(wait)} s kutiladi")
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


async def _claude_call(model, system, messages, max_tokens) -> str:
    resp = await _claude.messages.create(model=model, max_tokens=max_tokens, system=system, messages=messages)
    STATS["claude"] += 1
    return "".join(b.text for b in resp.content if b.type == "text")


async def cheap(system: str, messages: list, max_tokens: int = 1500, json_mode: bool = True) -> str:
    """E'lonlarni tasniflash, joy nomlari, so'rovni tushunish kabi oddiy ishlar."""
    for name, url, key, model in _providers():
        try:
            out = await _openai_compat(url, key, model, system, messages, max_tokens, json_mode)
            STATS[name] += 1
            return out
        except Exception as e:
            STATS["errors"] += 1
            logging.warning("%s xatosi, keyingisiga o'tildi: %s", name, e)
    return await _claude_call(config.EXTRACT_MODEL, system, messages, max_tokens)


async def deal(system: str, messages: list, max_tokens: int = 900) -> str:
    """E'lon egalari bilan suhbat. DEAL_PROVIDER=cheap bo'lsa arzon zanjir, aks holda Claude (DEAL_MODEL)."""
    if config.DEAL_PROVIDER == "cheap" and cheap_enabled():
        for name, url, key, model in _providers():
            try:
                out = await _openai_compat(url, key, model, system, messages, max_tokens, True)
                STATS[name] += 1
                return out
            except Exception as e:
                logging.warning("%s (suhbat) xatosi: %s", name, e)
    return await _claude_call(config.DEAL_MODEL, system, messages, max_tokens)
