"""Sozlamalar va hujjat turlari. Kalitlar .env faylidan o'qiladi."""
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

# Aniqlik yetmasa EXTRACT_MODEL ni "claude-sonnet-5-5" ga almashtiring (qimmatroq, lekin aniqroq)
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "claude-haiku-5-5")
CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-haiku-5-5")

DB_PATH = os.getenv("DB_PATH", "logistika.db")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent"))
NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "9"))  # eslatmalar soat nechada yuboriladi

# Botdan foydalana oladigan Telegram ID lar. Bo'sh bo'lsa, hamma kira oladi.
ALLOWED_USERS = {
    int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x.isdigit()
}

# --- Rang chegaralari (kun) ---
RED_DAYS = 7        # 7 kun va undan kam (yoki muddati o'tgan) -> qizil
YELLOW_DAYS = 30    # 30 kungacha -> sariq, undan ko'p -> yashil

# Hujjat muddati tugashidan necha kun oldin eslatish
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

# Muddati odatda yo'q hujjatlar (faqat "bor/yo'q" deb ko'rsatiladi)
NO_EXPIRY_TYPES = {"texpasport"}

# Bitta egasida bir nechta bo'la oladigan hujjatlar
MULTI_DOC_TYPES = {"plastik_karta"}

# --- Eslatma turlari ---
REMINDER_LABELS = {
    "china_entry": "Xitoyga kirish uchun onlayn navbat olish",
    "russia_exit": "Rossiyaga chiqish uchun onlayn navbat olish",
    "uz_entry": "O'zbekistonga kirish uchun onlayn navbat olish",
    "debt": "Qarzdor kompaniya to'lovini eslatish",
    "custom": "Vazifa",
}
RUSSIA_AFTER_CHINA_DAYS = 3  # Rossiya eslatmasi Xitoy navbati sanasidan necha kun keyin boshlanadi
