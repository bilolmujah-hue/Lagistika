"""Umumiy narsalar: bot obyekti, foydalanuvchi kiritish holati (WAIT), shifrlash, xabar yuborish."""
import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

import config

bot = Bot(config.BOT_TOKEN or "0:missing")

# Foydalanuvchidan matn kutilayotgan holatlar: user_id -> {"k": "turi", ...qo'shimcha}
WAIT: dict = {}


def wait_kind(uid) -> str:
    return (WAIT.get(uid) or {}).get("k", "")


# ------------------------------------------------------------------ shifrlash

def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(config.SESSION_KEY.encode())


def enc(s):
    if not s or not config.SESSION_KEY:
        return s
    return "enc:" + _fernet().encrypt(s.encode()).decode()


def dec(s):
    if not s:
        return s
    if s.startswith("enc:"):
        return _fernet().decrypt(s[4:].encode()).decode()
    return s


# ------------------------------------------------------------------ yordamchilar

def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def money(n) -> str:
    try:
        return f"{int(round(float(n))):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


async def send(uid, text, kb=None, html_mode=False, preview=False) -> bool:
    """Xabar yuboradi (uzun bo'lsa bo'lib). Tugma xato bersa - tugmasiz qayta urinadi."""
    ok = True
    parts = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    for n, part in enumerate(parts):
        last = n == len(parts) - 1
        try:
            await bot.send_message(uid, part, reply_markup=kb if last else None,
                                   parse_mode="HTML" if html_mode else None,
                                   disable_web_page_preview=not preview)
        except Exception as e:
            logging.warning("Xabar yuborilmadi (%s): %s", uid, e)
            ok = False
    return ok


async def notify_admins(text, kb=None, html_mode=False):
    import database as dbm
    for aid in dbm.admin_ids():
        await send(aid, text, kb, html_mode)


async def safe_edit(msg, text, kb=None, html_mode=False):
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML" if html_mode else None,
                            disable_web_page_preview=True)
    except TelegramBadRequest:
        pass
    except Exception:
        logging.debug("edit xato", exc_info=True)
