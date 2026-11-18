"""Sozlamalar va hujjat turlari. Kalitlar .env (yoki Railway Variables) dan o'qiladi.

Narx, sinov muddati, karta raqami kabi o'zgaruvchan sozlamalar ADMIN PANELDAN o'zgartiriladi
(bazadagi `settings` jadvalida saqlanadi). Bu yerdagi DEFAULTS - faqat boshlang'ich qiymatlar.
"""
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _ids(s: str) -> set:
    return {int(x) for x in (s or "").replace(" ", "").split(",") if x.lstrip("-").isdigit()}


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

# BOSH ADMIN(lar). Eski ALLOWED_USERS o'zgaruvchisi endi "bosh admin" degani - bot esa hammaga ochiq.
SUPER_ADMINS = _ids(os.getenv("ALLOWED_USERS", "")) | _ids(os.getenv("SUPER_ADMINS", ""))


def is_super(uid: int) -> bool:
    return uid in SUPER_ADMINS


# GPS (Wialon). Har bir foydalanuvchining o'z tokeni bazada saqlanadi.
# WIALON_TOKEN (ixtiyoriy) - faqat bosh admin uchun zaxira token (eski sozlama bilan moslik).
WIALON_TOKEN = os.getenv("WIALON_TOKEN", "").strip()
WIALON_URL = os.getenv("WIALON_URL", "https://1.gpsmonitor.uz/wialon/ajax.html").strip()
GPS_DEFAULT_SERVER = os.getenv("GPS_DEFAULT_SERVER", "https://1.gpsmonitor.uz").strip()

# AI modellari
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "claude-haiku-5-5")   # hujjat/chek o'qish, tasniflash
CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-haiku-5-5")         # bot ichidagi suhbat
DEAL_MODEL = os.getenv("DEAL_MODEL", "claude-sonnet-5-5")        # yuk egalari bilan suhbat

DB_PATH = os.getenv("DB_PATH", "logistika.db")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent"))
NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "9"))

# Telegram userbot (Telethon): my.telegram.org -> API development tools
TG_API_ID = int(os.getenv("TG_API_ID", "0") or 0)
TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
# Sessiyalar, GPS token va parollarni bazada shifrlash kaliti (Fernet). MAJBURIY TAVSIYA ETILADI.
# Yaratish: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SESSION_KEY = os.getenv("SESSION_KEY", "").strip()

COMPANY_NAME = os.getenv("COMPANY_NAME", "logistika kompaniyamiz")
CARD_BOT = os.getenv("CARD_BOT", "CardXabarBot").lstrip("@").lower()  # kartaga tushumlarni xabar qiluvchi bot

# --- Admin paneldan o'zgartiriladigan sozlamalarning boshlang'ich qiymatlari ---
DEFAULTS = {
    "price": "100000",          # 1 davr narxi (so'm)
    "period_days": "30",        # 1 to'lov necha kun beradi
    "trial_days": "10",         # yangi menejerga bepul sinov
    "pay_window_min": "10",     # to'lov uchun beriladigan vaqt (daqiqa)
    "card_number": "",
    "card_holder": "",
    "auto_pay": "1",            # 1 = CardXabar orqali avtomatik tekshirish yoqilgan
    "support": "",              # yordam uchun @username
    "user_dm_day": "25",        # bitta foydalanuvchi uchun kuniga eng ko'p yuk egasiga yozish
}

# --- Yuk qidirish (userbot pool) ---
SEARCH_HOURS = 24
MAX_DMS_PER_ACCOUNT_DAY = int(os.getenv("MAX_DMS_PER_ACCOUNT_DAY", "15"))  # 1 userbot kuniga nechta YANGI odamga yozadi
MAX_DMS_PER_SEARCH = int(os.getenv("MAX_DMS_PER_SEARCH", "8"))
DM_DELAY = (60, 180)          # bitta userbotdan yangi odamlarga yozish orasidagi kutish (soniya)
MAX_AI_TURNS = 12
LEAD_COOLDOWN_DAYS = 7        # bir odamga shuncha kun ichida qayta yozilmaydi (barcha foydalanuvchilar bo'yicha)

# --- Avto xabar (guruhlarga e'lon) ---
AP_INTERVALS = (5, 10, 15, 30, 60)                                  # daqiqa
AP_MAX_PER_GROUP_DAY = int(os.getenv("AP_MAX_PER_GROUP_DAY", "20"))  # 1 guruhga kuniga eng ko'pi
AP_MAX_GROUPS = int(os.getenv("AP_MAX_GROUPS", "30"))

# --- Rang chegaralari (kun) ---
RED_DAYS = 7
YELLOW_DAYS = 30
DOC_NOTIFY_DAYS = [30, 15, 7, 3, 1, 0]

# --- A) Texnika hujjatlari ---
VEHICLE_DOCS = {
    "texpasport": "Texpasport",
    "dopusk": "Dopusk (Допущения)",
    "litsenziya": "Litsenziya",
    "tahograf": "Tahograf sertifikati",
    "texkorik": "Texnik ko'rik (Техосмотр)",
    "tir": "TIR",
    "dazvol": "Dazvol (ruxsatnoma)",
    "sugurta_uz": "Sug'urta - O'zbekiston",
    "sugurta_kz": "Sug'urta - Qozog'iston",
    "sugurta_ru": "Sug'urta - Rossiya",
    "yashil_karta": "Yashil karta (Xitoy)",
}
VEHICLE_DOCS_SHORT = {
    "texpasport": "Texpasport",
    "dopusk": "Dopusk",
    "litsenziya": "Litsenziya",
    "tahograf": "Tahograf",
    "texkorik": "Texnik\nko'rik",
    "tir": "TIR",
    "dazvol": "Dazvol",
    "sugurta_uz": "Sug'urta\nO'zbekiston",
    "sugurta_kz": "Sug'urta\nQozog'iston",
    "sugurta_ru": "Sug'urta\nRossiya",
    "yashil_karta": "Yashil karta\n(Xitoy)",
}

# --- B) Haydovchi hujjatlari ---
DRIVER_DOCS = {
    "zagranpasport": "Zagranpasport",
    "prava": "Haydovchilik guvohnomasi (Prava)",
    "milliy_pasport": "Milliy pasport",
    "shengen": "Shengen viza",
    "kod95": "Kod 95 sertifikati",
    "adr": "ADR sertifikati",
    "chip_karta": "Chip karta (tahograf kartasi)",
    "plastik_karta": "Plastik karta",
}
DRIVER_DOCS_SHORT = {
    "zagranpasport": "Zagran-\npasport",
    "prava": "Prava",
    "milliy_pasport": "Milliy\npasport",
    "shengen": "Shengen\nviza",
    "kod95": "Kod 95",
    "adr": "ADR",
    "chip_karta": "Chip\nkarta",
    "plastik_karta": "Plastik\nkarta",
}

ALL_DOCS = {**VEHICLE_DOCS, **DRIVER_DOCS}
NO_EXPIRY_TYPES = {"texpasport"}
MULTI_DOC_TYPES = {"plastik_karta"}

REMINDER_LABELS = {
    "china_entry": "Xitoyga kirish uchun onlayn navbat olish",
    "russia_exit": "Rossiyaga chiqish uchun onlayn navbat olish",
    "uz_entry": "O'zbekistonga kirish uchun onlayn navbat olish",
    "debt": "Qarzdor kompaniya to'lovini eslatish",
    "custom": "Vazifa",
}
RUSSIA_AFTER_CHINA_DAYS = 3
