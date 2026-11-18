"""Telegram akkauntni ulash jarayoni (telefon -> kod -> 2FA parol). Kod va parol AI ga yuborilmaydi
va chatdan darhol o'chiriladi."""
import logging
import re

from aiogram.types import Message

import core
import database as dbm
import pickers
import userbot

TITLES = {
    "pool": "yuk qidirish userboti",
    "pay": "to'lov akkaunti (@CardXabarBot ulangan akkaunt)",
    "user": "shaxsiy Telegram akkauntingiz",
}


async def begin(m: Message, uid: int, target: str):
    core.WAIT[uid] = {"k": "login", "target": target, "step": "phone"}
    warn = ("⚠️ Ulangan akkaunt orqali bot sizning nomingizdan guruhlarga xabar yuboradi. "
            "Kod va parol hech kimga (AI ga ham) yuborilmaydi, xabaringiz darhol o'chiriladi.\n"
            if target == "user" else
            "⚠️ Bu akkauntga bot to'liq kiradi. Asosiy shaxsiy raqamingizni emas, alohida raqam ishlatish tavsiya etiladi.\n")
    await m.answer(
        f"🔗 Ulash: {TITLES[target]}\n\n"
        "1) Akkaunt telefon raqamini yuboring (masalan +998901234567).\n\n"
        f"{warn}Bekor qilish: /cancel")


async def step(m: Message, w: dict):
    uid = m.from_user.id
    text = (m.text or "").strip()
    st = w.get("step")
    try:
        if st == "phone":
            phone = "+" + re.sub(r"\D", "", text)
            if len(phone) < 10:
                await m.answer("Raqam noto'g'ri. Masalan: +998901234567")
                return
            await userbot.login_start(uid, phone)
            w["step"] = "code"
            await m.answer(
                "📲 Telegram shu akkauntga kod yubordi (ilovadagi «Telegram» rasmiy chatiga qarang).\n\n"
                "2) Kodni raqamlar orasiga tire qo'yib yuboring, masalan: 1-2-3-4-5\n"
                "(Oddiy yozilgan kodni Telegram xavfsizlik uchun bekor qiladi.)")
        elif st == "code":
            code = re.sub(r"\D", "", text)
            if not code:
                await m.answer("Kodni raqamlarda yuboring, masalan: 1-2-3-4-5")
                return
            res = await userbot.login_code(uid, code)
            if res == "password":
                w["step"] = "password"
                await m.answer("🔐 Akkauntda 2 bosqichli parol bor. 3) Shu parolni yuboring:")
            else:
                await _done(m, uid, w["target"])
        elif st == "password":
            await userbot.login_password(uid, text)
            await _done(m, uid, w["target"])
    except Exception as e:
        logging.warning("Akkaunt ulashda xato: %s", e)
        msg = userbot.describe_error(e)
        if "eskirgan" in msg or "topilmadi" in msg:
            w["step"] = "phone"
        await m.answer("❌ " + msg)
    finally:
        try:
            await m.delete()
        except Exception:
            pass


async def _done(m: Message, uid: int, target: str):
    core.WAIT.pop(uid, None)
    aid = await userbot.login_finish(uid, target)
    acc = dbm.acc_get(aid)
    who = f"{acc['name']}" + (f" (@{acc['username']})" if acc.get("username") else "")
    if target == "pool":
        await m.answer(f"✅ Userbot qo'shildi: {who}\n\nEndi bu akkaunt kuzatadigan yuk guruhlarini tanlang 👇")
        await pickers.show(m, uid, "p", aid, 0, refresh=True)
    elif target == "pay":
        import payments
        ok = await payments.check_card_bot()
        await m.answer(
            f"✅ To'lov akkaunti ulandi: {who}\n\n" +
            ("✅ @CardXabarBot bilan chat topildi. Kartaga tushumlar avtomatik kuzatiladi."
             if ok else
             "⚠️ Bu akkauntda @CardXabarBot bilan chat topilmadi. Shu akkauntdan @CardXabarBot ga kirib, "
             "kartangizni ulang - bot tushumlarni shu chat orqali o'qiydi."))
    elif target == "user":
        dbm.ap_ensure(uid)
        await m.answer(f"✅ Akkaunt ulandi: {who}\n\nEndi avto xabar yuboriladigan guruhlarni tanlang 👇")
        await pickers.show(m, uid, "a", aid, 0, refresh=True)
