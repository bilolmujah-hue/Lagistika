"""Qo'llanma video: admin yuklaydi (🛠 Admin panel → 📹 Qo'llanma), foydalanuvchi /start va /help da ko'radi."""
import logging

import core
import database as dbm

bot = core.bot

GUIDE_DEFAULT = ("📹 Qo'llanma: bot qanday ishlaydi\n\n"
                 "Bu qisqa videoda botning asosiy imkoniyatlari ko'rsatilgan: hujjat qo'shish va muddat eslatmalari, "
                 "GPS, yuk qidirish va e'lon egalari bilan avtomatik suhbat, guruhlarga avto xabar.\n\n"
                 "Savollar bo'lsa - menga oddiy yozing, sun'iy intellekt tushunadi 🤖")


def guide_text() -> str:
    return (dbm.setting("guide_text") or "").strip() or GUIDE_DEFAULT


async def send_guide(chat_id: int) -> bool:
    """Qo'llanma videosini (ostida matn bilan) yuboradi. Video yuklanmagan bo'lsa False."""
    fid, kind = dbm.setting("guide_file"), dbm.setting("guide_kind") or "video"
    if not fid:
        return False
    text = guide_text()
    cap = text if len(text) <= 1000 else None
    try:
        if kind == "animation":
            await bot.send_animation(chat_id, fid, caption=cap)
        elif kind == "document":
            await bot.send_document(chat_id, fid, caption=cap)
        else:
            await bot.send_video(chat_id, fid, caption=cap, supports_streaming=True)
        if cap is None:
            await core.send(chat_id, text)
        return True
    except Exception:
        logging.warning("Qo'llanma videosini yuborib bo'lmadi", exc_info=True)
        return False
