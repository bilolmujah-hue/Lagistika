"""Claude AI: (1) hujjat va to'lov chekini o'qish, (2) baza + GPS + yuk qidirish + avto xabar + (admin uchun)
boshqaruvga ulangan suhbat (tool use). Har bir rol faqat o'ziga ruxsat etilgan tool'larni ko'radi:
menejer - hammasi, haydovchi - faqat o'z hujjatlari va furasi, admin - qo'shimcha boshqaruv tool'lari."""
import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from anthropic import AsyncAnthropic

import autopost
import cargo
import config
import core
import database as dbm
import gps
import llm

client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY or "missing")


def _parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("AI javobida JSON topilmadi")
    return json.loads(text[start:end + 1])


def _media_block(data: bytes, mime: str) -> dict:
    b64 = base64.standard_b64encode(data).decode()
    if mime == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}


async def _ask(model, content, max_tokens=1200, system=None) -> str:
    kw = {"system": system} if system else {}
    resp = await client.messages.create(model=model, max_tokens=max_tokens,
                                        messages=[{"role": "user", "content": content}], **kw)
    return "".join(b.text for b in resp.content if b.type == "text")


# ====================================================================== 1) HUJJATNI O'QISH

def _extract_prompt(hint: str) -> str:
    veh = "\n".join(f'  - "{k}": {v}' for k, v in config.VEHICLE_DOCS.items())
    drv = "\n".join(f'  - "{k}": {v}' for k, v in config.DRIVER_DOCS.items())
    hint_text = f'\nFoydalanuvchi izohi (ishonchli): "{hint}"' if hint else ""
    return f"""Siz yuk tashish (logistika) kompaniyasi hujjatlarini o'qiydigan yordamchisiz.
Hujjatlar o'zbek, rus, qozoq, xitoy yoki ingliz tilida bo'lishi mumkin.

Bu hujjat qaysi turga kirishini aniqlang. Fura (transport vositasi) hujjatlari:
{veh}
Haydovchi hujjatlari:
{drv}
{hint_text}

FAQAT bitta JSON qaytaring (izoh va ``` belgilarisiz), shu kalitlar bilan:
{{
  "doc_type": "yuqoridagi kalitlardan biri yoki \\"unknown\\"",
  "plate": "fura davlat raqami, bo'shliqsiz, hujjatda qanday yozilgan bo'lsa shunday (fura hujjati bo'lmasa null)",
  "vehicle_model": "marka va model (masalan MAN TGX 18.510) yoki null",
  "vin": "VIN raqami yoki null",
  "driver_name": "haydovchi to'liq ismi lotin harfda (haydovchi hujjati bo'lmasa null)",
  "doc_number": "hujjat/polis/guvohnoma raqami yoki null",
  "issue_date": "YYYY-MM-DD yoki null",
  "expiry_date": "YYYY-MM-DD yoki null",
  "issuer": "hujjatni bergan tashkilot yoki null",
  "notes": "muhim qo'shimcha ma'lumot (masalan sug'urta summasi) yoki null",
  "confidence": "high | medium | low"
}}

Qoidalar:
- Sanalar hujjatda odatda KUN.OY.YIL tartibida yoziladi. JSON da YYYY-MM-DD qilib yozing.
- expiry_date = hujjat amal qiladigan OXIRGI kun. Sug'urta polisida "по 06.10.2027" bo'lsa, 2027-10-06.
- Raqamlar, VIN va davlat raqamini harfma-harf, o'zgartirmasdan ko'chiring.
- Ko'rinmagan yoki o'qib bo'lmaydigan joyga null yozing. HECH QACHON taxmin qilmang.
- Hujjat yuqoridagi turlarga kirmasa, doc_type = "unknown"."""


async def extract_document(data: bytes, mime: str, hint: str = "") -> dict:
    text = await _ask(config.EXTRACT_MODEL, [_media_block(data, mime), {"type": "text", "text": _extract_prompt(hint)}],
                      1500)
    return _parse_json(text)


async def extract_receipt(data: bytes, mime: str) -> dict:
    """To'lov cheki (Click, Payme, Uzum, bank ilovasi skrinshoti) dan ma'lumot."""
    prompt = """Bu rasm - O'zbekistondagi to'lov ilovasi (Click, Payme, Uzum, Paynet, bank ilovasi) cheki/kvitansiyasi
bo'lishi kerak. FAQAT bitta JSON qaytaring:
{"is_receipt": true/false (bu haqiqatan pul o'tkazmasi cheki/skrinshotimi),
 "amount": o'tkazilgan summa so'mda, faqat raqam (masalan 100347) yoki null,
 "currency": "UZS" yoki boshqasi,
 "date": "YYYY-MM-DD" yoki null, "time": "HH:MM" yoki null,
 "receiver_card_last4": "qabul qiluvchi kartaning oxirgi 4 raqami" yoki null,
 "receiver_name": "qabul qiluvchi ismi" yoki null,
 "status": "success | failed | pending | unknown",
 "app": "ilova nomi" yoki null}
Komissiyani summaga qo'shmang - o'tkazma summasini yozing. Ko'rinmasa null yozing, taxmin qilmang."""
    text = await _ask(config.EXTRACT_MODEL, [_media_block(data, mime), {"type": "text", "text": prompt}], 600)
    return _parse_json(text)


async def parse_cargo_query(text: str) -> dict:
    """'Toshkent Moskva yuk kerak' -> {origin, destination, mode, note}"""
    prompt = f"""Logistika botida foydalanuvchi qidiruv so'rovi yozdi: {json.dumps(text, ensure_ascii=False)}
FAQAT JSON qaytaring:
{{"origin": "jo'natish joyi yoki null", "destination": "manzil yoki null",
 "mode": "cargo yoki truck",
 "note": "narx, yuk turi, mashina turi kabi qo'shimcha shartlar yoki bo'sh"}}
mode: foydalanuvchining FURASI bor va YUK qidiryapti ("yuk kerak", "yuk qidiryapman", "груз нужен") -> "cargo";
foydalanuvchining YUKI bor va MASHINA qidiryapti ("fura kerak", "mashina kerak", "машина нужна", "tent kerak") -> "truck".
Aniq bo'lmasa "cargo". Ikki joy yozilgan bo'lsa birinchisi - origin, ikkinchisi - destination."""
    try:
        return _parse_json(await llm.cheap("Faqat JSON qaytaring.", [{"role": "user", "content": prompt}], 300))
    except Exception:
        logging.warning("Qidiruv so'rovini tahlil qilib bo'lmadi", exc_info=True)
        parts = text.split()
        return {"origin": parts[0] if parts else None, "destination": parts[1] if len(parts) > 1 else None,
                "mode": "truck" if "fura" in text.lower() or "mashina" in text.lower() else "cargo", "note": ""}


async def check_ad_text(text: str) -> dict:
    """Avto xabar matnini tekshiradi: logistika e'lonimi, firibgarlik/haqorat yo'qmi."""
    prompt = f"""Logistika (yuk tashish) Telegram guruhlariga muntazam yuboriladigan e'lon matni:
{json.dumps(text[:1500], ensure_ascii=False)}
Tekshiring: bu oddiy logistika e'lonimi (yuk bor / fura bor / xizmat taklifi)? Unda haqorat, firibgarlik,
noqonuniy narsa, boshqa odamlarning shaxsiy ma'lumotlari yoki spam-havolalar yo'qmi?
FAQAT JSON: {{"ok": true/false, "reason": "rad etilsa sababi (o'zbekcha, qisqa)", "tip": "matnni yaxshilash bo'yicha 1 jumlali maslahat yoki bo'sh"}}"""
    try:
        return _parse_json(await llm.cheap("Faqat JSON qaytaring.", [{"role": "user", "content": prompt}], 300))
    except Exception:
        return {"ok": True, "reason": "", "tip": ""}


# ====================================================================== 2) SUHBAT (TOOL USE)

@dataclass
class ChatContext:
    user: dict
    tables: set = field(default_factory=set)  # "vehicle", "driver"
    show_pay: bool = False
    show_ap: bool = False
    show_gps_connect: bool = False

    @property
    def uid(self):
        return self.user["user_id"]

    @property
    def role(self):
        return self.user.get("role")

    @property
    def data_uid(self):
        """Bazadagi ma'lumot egasi: menejer uchun o'zi, haydovchi uchun - menejeri."""
        return self.user.get("manager_id") if self.role == "driver" else self.uid


def _t(name, desc, props=None, req=None):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props or {}, "required": req or []}}


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
DOC_KEYS = list(config.ALL_DOCS.keys())
DATE_HINT = "KUN.OY.YIL yoki YYYY-MM-DD; 'bugun' yoki 'kecha' ham bo'ladi"

MANAGER_TOOLS = [
    _t("vehicle_overview",
       "FURA HAQIDA TO'LIQ MA'LUMOT: bazadagi hujjatlari, haydovchisi, eslatmalari + GPS dan hozirgi joylashuvi, "
       "tezligi, bugungi va kechagi yurgan km, to'xtashlari. Foydalanuvchi fura raqamini yozsa DOIM shuni chaqiring.",
       {"query": S}, ["query"]),
    _t("gps_fleet", "Barcha mashinalarning hozirgi GPS holati: yurmoqda / to'xtagan / aloqa yo'q, tezlik, xarita."),
    _t("gps_vehicle", "Bitta mashina hozir qayerda: manzil, koordinata, tezlik, oxirgi signal.", {"query": S}, ["query"]),
    _t("gps_mileage",
       "Mashina qaysi kun(lar)da necha km yurgan, necha soat yurgan, qayerda to'xtagan. Bitta kun uchun date_from. "
       "Oraliq uchun date_to ham (eng ko'pi 31 kun). detail=true - bitta kun uchun to'xtashlar ro'yxati.",
       {"query": S, "date_from": {"type": "string", "description": DATE_HINT},
        "date_to": {"type": "string", "description": DATE_HINT}, "detail": B}, ["query"]),
    _t("gps_fleet_mileage", "Butun park: shu kuni har bir mashina necha km yurgan, jami km.",
       {"date": {"type": "string", "description": DATE_HINT}}),
    _t("gps_connect", "GPS ulanmagan bo'lsa yoki foydalanuvchi GPS ulamoqchi bo'lsa - GPS ulash oynasini ochish."),

    _t("list_vehicles", "Barcha furalar ro'yxati (haydovchisi bilan)."),
    _t("get_vehicle", "Bitta furaning bazadagi to'liq ma'lumoti (GPS siz).", {"query": S}, ["query"]),
    _t("list_drivers", "Barcha haydovchilar ro'yxati (botga ulanganmi - ham ko'rsatiladi)."),
    _t("get_driver", "Bitta haydovchining hujjatlari va muddatlari.", {"query": S}, ["query"]),
    _t("expiring_documents",
       "Muddati tugayotgan yoki o'tgan hujjatlar, eng yaqini birinchi. 'Eng kam muddati qolgan' uchun within_days=3650.",
       {"within_days": I, "owner_type": {"type": "string", "enum": ["vehicle", "driver", "all"]}}),
    _t("documents_by_type", "Bitta hujjat turi bo'yicha HAMMA furalar/haydovchilar ro'yxati va muddatlari.",
       {"doc_type": {"type": "string", "enum": DOC_KEYS}}, ["doc_type"]),
    _t("missing_documents", "Qaysi fura yoki haydovchida qaysi hujjatlar kiritilmagan.",
       {"owner_type": {"type": "string", "enum": ["vehicle", "driver"]}}, ["owner_type"]),

    _t("add_vehicle", "Yangi fura qo'shish.", {"plate": S, "model": S}, ["plate"]),
    _t("update_vehicle", "Fura raqami (plate), modeli yoki VIN ini o'zgartirish.",
       {"query": S, "field": {"type": "string", "enum": ["plate", "model", "vin"]}, "value": S},
       ["query", "field", "value"]),
    _t("delete_vehicle", "Furani butunlay o'chirish. FAQAT foydalanuvchi tasdiqlagandan keyin, confirm=true.",
       {"query": S, "confirm": B}, ["query", "confirm"]),
    _t("add_driver", "Yangi haydovchi qo'shish. Telefon berilsa - haydovchi shu raqam bilan botga kirganda "
                     "avtomatik ulanadi va o'z hujjatlarini ko'radi.", {"name": S, "phone": S}, ["name"]),
    _t("update_driver", "Haydovchining ismi (name) yoki telefonini (phone) o'zgartirish.",
       {"query": S, "field": {"type": "string", "enum": ["name", "phone"]}, "value": S}, ["query", "field", "value"]),
    _t("delete_driver", "Haydovchini butunlay o'chirish. FAQAT tasdiqdan keyin, confirm=true.",
       {"query": S, "confirm": B}, ["query", "confirm"]),
    _t("remove_driver_access", "Haydovchini botdan chiqarish (u endi hujjatlarni ko'rmaydi, yozuvi bazada qoladi).",
       {"query": S}, ["query"]),
    _t("assign_driver", "Haydovchini furaga biriktirish.", {"driver_name": S, "plate": S}, ["driver_name", "plate"]),
    _t("unassign_driver", "Furadan haydovchini ajratish.", {"plate": S}, ["plate"]),

    _t("save_document", "Hujjatni qo'lda qo'shish yoki yangilash. Sanalar KUN.OY.YIL yoki YYYY-MM-DD.",
       {"owner_type": {"type": "string", "enum": ["vehicle", "driver"]},
        "owner": {"type": "string", "description": "fura raqami yoki haydovchi ismi"},
        "doc_type": {"type": "string", "enum": DOC_KEYS},
        "doc_number": S, "issue_date": S, "expiry_date": S, "model": S},
       ["owner_type", "owner", "doc_type"]),
    _t("update_document", "Mavjud hujjatning bitta maydonini o'zgartirish.",
       {"doc_id": I, "field": {"type": "string", "enum": ["doc_number", "issue_date", "expiry_date", "issuer", "notes"]},
        "value": S}, ["doc_id", "field", "value"]),
    _t("delete_document", "Hujjatni o'chirish. doc_id yoki owner_type + owner + doc_type bering.",
       {"doc_id": I, "owner_type": {"type": "string", "enum": ["vehicle", "driver"]},
        "owner": S, "doc_type": {"type": "string", "enum": DOC_KEYS}}),

    _t("set_china_queue_date", "Furaning Xitoy navbati sanasi: Xitoyga kirish va (3 kundan keyin) Rossiyaga chiqish "
                               "eslatmalarini yaratadi.", {"plate": S, "date": S}, ["plate", "date"]),
    _t("add_reminder", "Sanali eslatma. kind: uz_entry, debt, custom.",
       {"kind": {"type": "string", "enum": ["uz_entry", "debt", "custom"]},
        "title": {"type": "string", "description": "qisqa tavsif"}, "date": S,
        "plate": {"type": "string", "description": "fura raqami (ixtiyoriy)"}}, ["kind", "date"]),
    _t("list_reminders", "Bajarilmagan eslatmalar."),
    _t("update_reminder", "Eslatma sanasi (remind_date) yoki matnini (title) o'zgartirish.",
       {"reminder_id": I, "field": {"type": "string", "enum": ["remind_date", "title"]}, "value": S},
       ["reminder_id", "field", "value"]),
    _t("complete_reminder", "Eslatmani bajarilgan deb belgilash.", {"reminder_id": I}, ["reminder_id"]),
    _t("delete_reminder", "Eslatmani o'chirish.", {"reminder_id": I}, ["reminder_id"]),

    _t("cargo_search",
       "YUK yoki FURA QIDIRISH yuk guruhlaridan. mode='cargo' - foydalanuvchining furasi bor, YUK kerak "
       "('Toshkentdan Moskvaga yuk kerak'); mode='truck' - foydalanuvchining yuki bor, MASHINA kerak "
       "('Andijondan Almatiga fura kerak'). Natijani bot o'zi keyin yuboradi (tool darhol qaytadi).",
       {"origin": {"type": "string", "description": "FAQAT joy nomi: shahar, viloyat yoki davlat (masalan 'Pskov'). "
                                                     "Koordinata, qavs va izoh yozmang."},
        "destination": {"type": "string", "description": "FAQAT joy nomi"},
        "mode": {"type": "string", "enum": ["cargo", "truck"]},
        "note": S, "hours": {"type": "integer", "description": "necha soat orqaga (odatda 24)"}}, ["origin"]),
    _t("cargo_status", "Qidiruvlar va e'lon egalari bilan suhbatlar holati."),
    _t("cargo_cancel", "Yuk/fura qidiruvini va e'lon egalariga yozishni DARHOL TO'XTATISH. Foydalanuvchi 'bekor qil', "
                       "'to'xtat', 'qidirma', 'yozma' desa shuni chaqiring. stop_chats=true - boshlangan AI suhbatlarini "
                       "ham to'xtatadi (foydalanuvchi 'hammasini to'xtat', 'gaplashma' desa).",
       {"stop_chats": B}),

    _t("autopost_status", "Avto xabar (guruhlarga e'lon yuborish) holati: ulangan akkaunt, guruhlar, matn, bugun nechta."),
    _t("autopost_control", "Avto xabarni boshqarish: action=start | stop | set_text | set_interval | open_menu. "
                           "set_text uchun text, set_interval uchun minutes (5,10,15,30,60).",
       {"action": {"type": "string", "enum": ["start", "stop", "set_text", "set_interval", "open_menu"]},
        "text": S, "minutes": I}, ["action"]),

    _t("subscription_status", "Foydalanuvchi obunasi: sinov/to'langan, qachon tugaydi, narx."),
    _t("open_payment", "To'lov qilish oynasini ochish (foydalanuvchi to'lamoqchi bo'lsa)."),
    _t("set_company_name", "Kompaniya nomini saqlash (yuk egalari bilan suhbatda shu nom ishlatiladi).",
       {"name": S}, ["name"]),

    _t("show_table", "Rangli jadvalni (rasm) yuborish. Jadval so'ralganda DOIM shuni chaqiring.",
       {"kind": {"type": "string", "enum": ["vehicle", "driver", "both"]}}, ["kind"]),
]

DRIVER_TOOLS = [
    _t("my_info", "Haydovchining o'z hujjatlari, unga biriktirilgan fura(lar) va ularning hujjatlari, eslatmalar."),
    _t("my_vehicle_location", "Menga biriktirilgan fura hozir qayerda (GPS)."),
    _t("my_vehicle_mileage", "Menga biriktirilgan fura necha km yurgan (bugun/kecha/oraliq).",
       {"date_from": {"type": "string", "description": DATE_HINT},
        "date_to": {"type": "string", "description": DATE_HINT}}),
]

ADMIN_TOOLS = [
    _t("admin_stats", "ADMIN: bot statistikasi - foydalanuvchilar, obunalar, daromad."),
    _t("admin_find_user", "ADMIN: foydalanuvchini topish (ID, @username, telefon yoki ism).", {"query": S}, ["query"]),
    _t("admin_user_action", "ADMIN: foydalanuvchi ustida amal: block, unblock, vip_on, vip_off, extend (days), end_sub.",
       {"user_id": I, "action": {"type": "string", "enum": ["block", "unblock", "vip_on", "vip_off", "extend", "end_sub"]},
        "days": I}, ["user_id", "action"]),
]

STATUS_UZ = {"missing": "yo'q", "noexp": "muddatsiz", "expired": "muddati o'tgan",
             "red": "7 kundan kam", "yellow": "30 kundan kam", "green": "yaxshi"}


# ------------------------------------------------------------------ yordamchilar

def _gdate(s):
    s = (s or "").strip().lower()
    if s in ("", "bugun", "today"):
        return dbm.today()
    if s in ("kecha", "yesterday"):
        return dbm.today() - timedelta(days=1)
    iso = dbm.parse_date(s)
    return date.fromisoformat(iso) if iso else None


def _doc_list(uid, owner_type, owner_id, labels):
    by = {}
    for r in dbm.owner_doc_rows(uid, owner_type, owner_id):
        by.setdefault(r["doc_type"], []).append(r)
    out = []
    for key, label in labels.items():
        rows = by.get(key) or [None]
        if key not in config.MULTI_DOC_TYPES:
            rows = rows[-1:]
        for r in rows:
            st, d = dbm.doc_status(r)
            out.append({
                "doc_id": r["id"] if r else None, "hujjat": label,
                "raqam": r["doc_number"] if r else None,
                "berilgan_sana": dbm.fmt_date(r["issue_date"]) if r and r["issue_date"] else None,
                "tugash_sanasi": dbm.fmt_date(r["expiry_date"]) if r and r["expiry_date"] else None,
                "qolgan_kun": d, "holat": STATUS_UZ[st],
                "bergan_tashkilot": r["issuer"] if r else None,
                "izoh": r["notes"] if r else None,
            })
    return out


def _vehicle_info(uid, v):
    drv = dbm.get_driver(uid, v["driver_id"]) if v["driver_id"] else None
    rems = [{"id": r["id"], "eslatma": dbm.reminder_title(r), "sana": dbm.fmt_date(r["remind_date"])}
            for r in dbm.list_reminders(uid) if r["vehicle_id"] == v["id"]]
    return {"id": v["id"], "raqam": v["plate"], "model": v["model"], "vin": v["vin"],
            "haydovchi": drv["name"] if drv else None,
            "haydovchi_telefon": drv["phone"] if drv else None,
            "xitoy_navbat_sanasi": dbm.fmt_date(v["china_queue_date"]) if v["china_queue_date"] else None,
            "hujjatlar": _doc_list(uid, "vehicle", v["id"], config.VEHICLE_DOCS), "eslatmalar": rems}


def _owner_of(uid, owner_type, owner):
    return dbm.find_vehicle(uid, owner) if owner_type == "vehicle" else dbm.find_driver(uid, owner)


GPS_OFF = {"xato": "GPS ulanmagan. «📍 GPS» tugmasi orqali ulang (gps_connect tool'ini chaqiring)."}


async def _safe(coro):
    try:
        return await coro
    except Exception as e:
        logging.warning("GPS xatosi: %s", e)
        return {"xato": f"GPS: {e}"}


async def _vehicle_overview(ctx, query):
    uid = ctx.data_uid
    v = dbm.find_vehicle(uid, query)
    out = {"baza": _vehicle_info(uid, v) if v else {"xato": "Bazada bunday fura topilmadi"}}
    w = gps.client_for_user(ctx.user)
    if not w:
        out["gps"] = GPS_OFF
        return out
    plate = v["plate"] if v else query
    try:
        u = gps.find_unit(await gps.get_units(w), plate)
    except Exception as e:
        out["gps"] = {"xato": f"GPS tizimiga ulanib bo'lmadi: {e}"}
        return out
    if not u:
        out["gps"] = {"xato": "GPS tizimida bunday mashina topilmadi (Wialon'dagi nomda raqam bo'lishi kerak)"}
        return out
    today = dbm.today()
    now, bugun, kecha = await asyncio.gather(
        _safe(gps.unit_summary(u)),
        _safe(gps.day_report(w, u, today, detail=True)),
        _safe(gps.day_report(w, u, today - timedelta(days=1), detail=True)))
    out["gps"] = {"hozir": now, "bugun": bugun, "kecha": kecha}
    return out


def _missing(uid, owner_type):
    if owner_type == "vehicle":
        owners, labels, name = dbm.list_vehicles(uid), config.VEHICLE_DOCS, (lambda o: o["plate"])
    else:
        owners, labels, name = dbm.list_drivers(uid), config.DRIVER_DOCS, (lambda o: o["name"])
    by = dbm.docs_by_owner(uid, owner_type)
    res = []
    for o in owners:
        have = by.get(o["id"], {})
        miss = [lbl for k, lbl in labels.items() if k not in have]
        if miss:
            res.append({"egasi": name(o), "yoq_hujjatlar": miss})
    return res


def _docs_by_type(uid, doc_type):
    ot = "vehicle" if doc_type in config.VEHICLE_DOCS else "driver"
    owners = dbm.list_vehicles(uid) if ot == "vehicle" else dbm.list_drivers(uid)
    by = dbm.docs_by_owner(uid, ot)
    have, missing = [], []
    for o in owners:
        nm = o["plate"] if ot == "vehicle" else o["name"]
        r = by.get(o["id"], {}).get(doc_type)
        if not r:
            missing.append(nm)
            continue
        st, d = dbm.doc_status(r)
        have.append({"doc_id": r["id"], "egasi": nm, "raqam": r["doc_number"],
                     "tugash_sanasi": dbm.fmt_date(r["expiry_date"]) if r["expiry_date"] else None,
                     "qolgan_kun": d, "holat": STATUS_UZ[st]})
    have.sort(key=lambda x: (x["qolgan_kun"] is None, x["qolgan_kun"] if x["qolgan_kun"] is not None else 0))
    return {"hujjat": config.ALL_DOCS[doc_type], "bor": have, "yoq_bolganlar": missing}


async def link_after_phone(manager_id, rec_id) -> dict:
    """Haydovchiga telefon kiritilgach - shu raqam bilan botga kirgan haydovchini bog'laydi."""
    st, tid = dbm.try_link_record(manager_id, rec_id)
    d = dbm.get_driver(manager_id, rec_id)
    if st == "linked":
        mu = dbm.get_user(manager_id) or {}
        await core.send(tid, f"✅ Siz {mu.get('company') or mu.get('name') or 'menejer'} kompaniyasiga haydovchi "
                             f"sifatida ulandingiz ({d['name']}).\nEndi o'z hujjatlaringiz va furangizni ko'ra olasiz, "
                             "eslatmalar sizga ham keladi. /start")
        return {"botga_ulandi": True}
    if st == "taken":
        return {"botga_ulandi": False, "izoh": "Bu raqamli haydovchi boshqa kompaniyaga ulangan."}
    return {"botga_ulandi": False,
            "izoh": "Haydovchi shu raqam bilan botga kirib «Haydovchi» rolini tanlasa, avtomatik ulanadi."}


def driver_scope(u):
    """Haydovchi uchun: (menejer_id, haydovchi_yozuvi, unga biriktirilgan furalar)."""
    mid, rid = u.get("manager_id"), u.get("driver_rec_id")
    d = dbm.get_driver(mid, rid) if mid and rid else None
    vs = dbm.vehicles_of_driver(mid, rid) if d else []
    return mid, d, vs


# ------------------------------------------------------------------ asboblarni bajarish

async def run_tool(ctx: ChatContext, name: str, a: dict):
    try:
        if ctx.role == "driver":
            return await _run_driver_tool(ctx, name, a)
        if name.startswith("admin_"):
            if not dbm.is_admin(ctx.uid):
                return {"xato": "Ruxsat yo'q"}
            return await _run_admin_tool(ctx, name, a)
        return await _run_manager_tool(ctx, name, a)
    except Exception as e:  # noqa
        logging.exception("tool xatosi: %s", name)
        return {"xato": str(e)}


async def _run_driver_tool(ctx, name, a):
    mid, d, vs = driver_scope(ctx.user)
    if not d:
        return {"xato": "Siz hali hech qaysi menejerga ulanmagansiz."}
    if name == "my_info":
        rems = [{"eslatma": dbm.reminder_title(r), "fura": r["plate"], "sana": dbm.fmt_date(r["remind_date"])}
                for r in dbm.list_reminders(mid) if r["vehicle_id"] in {v["id"] for v in vs}]
        return {"ism": d["name"], "telefon": d["phone"],
                "hujjatlarim": _doc_list(mid, "driver", d["id"], config.DRIVER_DOCS),
                "furalarim": [{"raqam": v["plate"], "model": v["model"],
                               "hujjatlar": _doc_list(mid, "vehicle", v["id"], config.VEHICLE_DOCS)} for v in vs],
                "eslatmalar": rems}
    if name in ("my_vehicle_location", "my_vehicle_mileage"):
        if not vs:
            return {"xato": "Sizga fura biriktirilmagan."}
        w = gps.client_for_user(ctx.user)
        if not w:
            return {"xato": "Kompaniyangizda GPS ulanmagan."}
        units = await gps.get_units(w)
        res = []
        for v in vs:
            u = gps.find_unit(units, v["plate"])
            if not u:
                res.append({"fura": v["plate"], "xato": "GPS da topilmadi"})
            elif name == "my_vehicle_location":
                res.append(await gps.unit_summary(u))
            else:
                d1 = _gdate(a.get("date_from"))
                d2 = _gdate(a.get("date_to")) if a.get("date_to") else d1
                if not d1 or not d2:
                    return {"xato": "Sana noto'g'ri"}
                res.append(await gps.period_report(w, u, d1, min(d2, dbm.today())))
        return res
    return {"xato": "Bu amal haydovchi uchun mavjud emas."}


async def _run_admin_tool(ctx, name, a):
    if name == "admin_stats":
        return dbm.global_stats()
    if name == "admin_find_user":
        out = []
        for u in dbm.find_users(a["query"]):
            st, until = dbm.sub_state(u)
            out.append({"id": u["user_id"], "ism": u["name"], "username": u["username"], "telefon": u["phone"],
                        "rol": u["role"], "holat": st, "gacha": dbm.fmt_dt(until) if until else None,
                        "bloklangan": bool(u["blocked"]), "vip": bool(u["vip"])})
        return out or {"xato": "Topilmadi"}
    if name == "admin_user_action":
        uid, act = int(a["user_id"]), a["action"]
        if not dbm.get_user(uid):
            return {"xato": "Foydalanuvchi topilmadi"}
        if act == "block":
            if dbm.is_admin(uid):
                return {"xato": "Adminni bloklab bo'lmaydi"}
            dbm.set_user(uid, blocked=1)
        elif act == "unblock":
            dbm.set_user(uid, blocked=0)
        elif act == "vip_on":
            dbm.set_user(uid, vip=1)
            await core.send(uid, "⭐ Sizga VIP maqom berildi - bot siz uchun to'liq bepul!")
        elif act == "vip_off":
            dbm.set_user(uid, vip=0)
        elif act == "extend":
            until = dbm.extend_sub(uid, int(a.get("days") or 30))
            await core.send(uid, f"🎁 Obunangiz {dbm.fmt_dt(until)} gacha uzaytirildi.")
            return {"ok": True, "gacha": dbm.fmt_dt(until)}
        elif act == "end_sub":
            dbm.end_sub(uid)
        return {"ok": True}
    return {"xato": "Noma'lum admin tool"}


async def _run_manager_tool(ctx: ChatContext, name: str, a: dict):
    uid = ctx.uid
    # ---------------- GPS
    if name == "vehicle_overview":
        return await _vehicle_overview(ctx, a["query"])
    if name == "gps_connect":
        ctx.show_gps_connect = True
        return {"ok": True, "izoh": "GPS ulash oynasi yuboriladi. Foydalanuvchiga 1 jumla yozing."}
    if name.startswith("gps_"):
        w = gps.client_for_user(ctx.user)
        if not w:
            return GPS_OFF
        if name == "gps_fleet":
            units = await gps.get_units(w)
            return [{"gps_nomi": u["name"], "holat": gps.status_word(u), "tezlik_km_soat": u["speed"],
                     "oxirgi_signal": gps._ago(u),
                     "xarita": gps.maps_link(u) if u["lat"] is not None else None} for u in units]
        if name == "gps_vehicle":
            u = gps.find_unit(await gps.get_units(w), a["query"])
            return await gps.unit_summary(u) if u else {"xato": "GPS tizimida bunday mashina topilmadi"}
        if name == "gps_mileage":
            u = gps.find_unit(await gps.get_units(w), a["query"])
            if not u:
                return {"xato": "GPS tizimida bunday mashina topilmadi"}
            d1 = _gdate(a.get("date_from"))
            d2 = _gdate(a.get("date_to")) if a.get("date_to") else d1
            if not d1 or not d2:
                return {"xato": "Sana noto'g'ri. KUN.OY.YIL formatida yozing."}
            try:
                return await gps.period_report(w, u, d1, min(d2, dbm.today()), detail=bool(a.get("detail")))
            except ValueError as e:
                return {"xato": str(e)}
        if name == "gps_fleet_mileage":
            d = _gdate(a.get("date"))
            if not d:
                return {"xato": "Sana noto'g'ri"}
            return await gps.fleet_mileage(w, await gps.get_units(w), d)

    # ---------------- ro'yxatlar
    if name == "list_vehicles":
        res = []
        for v in dbm.list_vehicles(uid):
            drv = dbm.get_driver(uid, v["driver_id"]) if v["driver_id"] else None
            res.append({"id": v["id"], "raqam": v["plate"], "model": v["model"],
                        "haydovchi": drv["name"] if drv else None})
        return res
    if name == "get_vehicle":
        v = dbm.find_vehicle(uid, a["query"])
        return _vehicle_info(uid, v) if v else {"xato": "Bunday fura topilmadi"}
    if name == "list_drivers":
        return [{"id": d["id"], "ism": d["name"], "telefon": d["phone"], "botga_ulangan": bool(d.get("tg_user_id")),
                 "furalari": [v["plate"] for v in dbm.vehicles_of_driver(uid, d["id"])]}
                for d in dbm.list_drivers(uid)]
    if name == "get_driver":
        d = dbm.find_driver(uid, a["query"])
        if not d:
            return {"xato": "Bunday haydovchi topilmadi"}
        return {"id": d["id"], "ism": d["name"], "telefon": d["phone"], "botga_ulangan": bool(d.get("tg_user_id")),
                "furalari": [v["plate"] for v in dbm.vehicles_of_driver(uid, d["id"])],
                "hujjatlar": _doc_list(uid, "driver", d["id"], config.DRIVER_DOCS)}
    if name == "expiring_documents":
        rows = dbm.expiring(uid, int(a.get("within_days", 30)),
                            None if a.get("owner_type") in (None, "all") else a["owner_type"])
        return [{"doc_id": r["id"], "egasi": r["owner_name"],
                 "tur": "fura" if r["owner_type"] == "vehicle" else "haydovchi",
                 "hujjat": r["doc_label"], "tugash_sanasi": dbm.fmt_date(r["expiry_date"]),
                 "qolgan_kun": r["days_left"]} for r in rows[:60]]
    if name == "documents_by_type":
        return _docs_by_type(uid, a["doc_type"])
    if name == "missing_documents":
        return _missing(uid, a["owner_type"])

    # ---------------- furalar / haydovchilar
    if name == "add_vehicle":
        vid = dbm.get_or_create_vehicle(uid, a["plate"], a.get("model"))
        return {"ok": bool(vid), "fura_id": vid}
    if name == "update_vehicle":
        v = dbm.find_vehicle(uid, a["query"])
        if not v:
            return {"xato": "Bunday fura topilmadi"}
        res = dbm.update_vehicle(uid, v["id"], a["field"], a["value"])
        if res and res.get("error"):
            return {"xato": res["error"]}
        return {"ok": True, "fura": v["plate"], **(res or {})}
    if name == "delete_vehicle":
        v = dbm.find_vehicle(uid, a["query"])
        if not v:
            return {"xato": "Bunday fura topilmadi"}
        if not a.get("confirm"):
            return {"xato": f"{v['plate']} ni o'chirishdan oldin foydalanuvchidan tasdiq so'rang."}
        res = dbm.delete_vehicle(uid, v["id"])
        return {"ok": bool(res), "ochirildi": v["plate"], **(res or {})}
    if name == "add_driver":
        did = dbm.get_or_create_driver(uid, a["name"], a.get("phone"))
        out = {"ok": bool(did), "haydovchi_id": did}
        if did and a.get("phone"):
            out.update(await link_after_phone(uid, did))
        return out
    if name == "update_driver":
        d = dbm.find_driver(uid, a["query"])
        if not d:
            return {"xato": "Bunday haydovchi topilmadi"}
        res = dbm.update_driver(uid, d["id"], a["field"], a["value"])
        if res and res.get("error"):
            return {"xato": res["error"]}
        out = {"ok": True, "haydovchi": d["name"], **(res or {})}
        if a["field"] == "phone":
            out.update(await link_after_phone(uid, d["id"]))
        return out
    if name == "delete_driver":
        d = dbm.find_driver(uid, a["query"])
        if not d:
            return {"xato": "Bunday haydovchi topilmadi"}
        if not a.get("confirm"):
            return {"xato": f"{d['name']} ni o'chirishdan oldin foydalanuvchidan tasdiq so'rang."}
        tid = dbm.unlink_driver(uid, d["id"])
        if tid:
            await core.send(tid, "ℹ️ Menejeringiz sizni botdagi kompaniya ro'yxatidan chiqardi.")
        res = dbm.delete_driver(uid, d["id"])
        return {"ok": bool(res), "ochirildi": d["name"], **(res or {})}
    if name == "remove_driver_access":
        d = dbm.find_driver(uid, a["query"])
        if not d:
            return {"xato": "Bunday haydovchi topilmadi"}
        tid = dbm.unlink_driver(uid, d["id"])
        if not tid:
            return {"xato": f"{d['name']} botga ulanmagan."}
        await core.send(tid, "ℹ️ Menejeringiz sizni botdagi kompaniya ro'yxatidan chiqardi.")
        return {"ok": True, "chiqarildi": d["name"]}
    if name == "assign_driver":
        d = dbm.find_driver(uid, a["driver_name"])
        v = dbm.find_vehicle(uid, a["plate"])
        if not d or not v:
            return {"xato": "Haydovchi yoki fura topilmadi. Avval qo'shing."}
        dbm.assign_driver(uid, d["id"], v["id"])
        if d.get("tg_user_id"):
            await core.send(d["tg_user_id"], f"🚛 Sizga {v['plate']} furasi biriktirildi.")
        return {"ok": True, "haydovchi": d["name"], "fura": v["plate"]}
    if name == "unassign_driver":
        v = dbm.find_vehicle(uid, a["plate"])
        if not v:
            return {"xato": "Bunday fura topilmadi"}
        return {"ok": dbm.unassign_driver(uid, v["id"]), "fura": v["plate"]}

    # ---------------- hujjatlar
    if name == "save_document":
        ot, dt = a["owner_type"], a["doc_type"]
        cfg = config.VEHICLE_DOCS if ot == "vehicle" else config.DRIVER_DOCS
        if dt not in cfg:
            return {"xato": "Bu hujjat turi shu egasi uchun mavjud emas", "mumkin": list(cfg)}
        exp = dbm.parse_date(a.get("expiry_date")) if a.get("expiry_date") else None
        iss = dbm.parse_date(a.get("issue_date")) if a.get("issue_date") else None
        if (a.get("expiry_date") and not exp) or (a.get("issue_date") and not iss):
            return {"xato": "Sana noto'g'ri. KUN.OY.YIL formatida yozing."}
        oid = (dbm.get_or_create_vehicle(uid, a["owner"], a.get("model")) if ot == "vehicle"
               else dbm.get_or_create_driver(uid, a["owner"]))
        if not oid:
            return {"xato": "Egasi nomi noto'g'ri"}
        existing = dbm.owner_docs(uid, ot, oid).get(dt)
        if existing and dt not in config.MULTI_DOC_TYPES:
            for fld, val in (("doc_number", a.get("doc_number")), ("issue_date", iss), ("expiry_date", exp)):
                if val:
                    dbm.update_document(uid, existing["id"], fld, val)
            return {"ok": True, "doc_id": existing["id"], "yangilandi": True}
        return {"ok": True, "doc_id": dbm.save_document(uid, ot, oid, dt, a.get("doc_number"), iss, exp)}
    if name == "update_document":
        res = dbm.update_document(uid, int(a["doc_id"]), a["field"], a["value"])
        return {"ok": True, **res} if res else {"xato": "Hujjat topilmadi yoki qiymat noto'g'ri"}
    if name == "delete_document":
        if a.get("doc_id"):
            doc = dbm.get_document(uid, int(a["doc_id"]))
            if not doc:
                return {"xato": "Hujjat topilmadi"}
            ok = dbm.delete_document(uid, int(a["doc_id"]))
            return {"ok": ok, "ochirildi": config.ALL_DOCS.get(doc["doc_type"], doc["doc_type"])}
        if not (a.get("owner_type") and a.get("owner") and a.get("doc_type")):
            return {"xato": "doc_id yoki (owner_type, owner, doc_type) kerak"}
        o = _owner_of(uid, a["owner_type"], a["owner"])
        if not o:
            return {"xato": "Egasi topilmadi"}
        n = dbm.delete_documents_of_type(uid, a["owner_type"], o["id"], a["doc_type"])
        return {"ok": n > 0, "ochirilgan_soni": n}

    # ---------------- eslatmalar
    if name == "set_china_queue_date":
        v = dbm.find_vehicle(uid, a["plate"])
        d = dbm.parse_date(a["date"])
        if not v:
            return {"xato": "Bunday fura topilmadi"}
        if not d:
            return {"xato": "Sana noto'g'ri"}
        dbm.set_china_queue(uid, v["id"], d)
        return {"ok": True, "fura": v["plate"], "eslatmalar": [
            {"tur": "Xitoyga kirish navbati", "sana": dbm.fmt_date(d)},
            {"tur": "Rossiyaga chiqish navbati",
             "sana": dbm.fmt_date((date.fromisoformat(d) + timedelta(days=config.RUSSIA_AFTER_CHINA_DAYS)).isoformat())}],
            "izoh": "Furaga biriktirilgan haydovchiga ham eslatma boradi."}
    if name == "add_reminder":
        d = dbm.parse_date(a["date"])
        if not d:
            return {"xato": "Sana noto'g'ri"}
        vid = None
        if a.get("plate"):
            v = dbm.find_vehicle(uid, a["plate"])
            vid = v["id"] if v else None
        return {"ok": True, "eslatma_id": dbm.add_reminder(uid, a["kind"], a.get("title"), d, vid, pre_days=1)}
    if name == "list_reminders":
        return [{"id": r["id"], "eslatma": dbm.reminder_title(r), "fura": r["plate"],
                 "sana": dbm.fmt_date(r["remind_date"])} for r in dbm.list_reminders(uid)]
    if name == "update_reminder":
        res = dbm.update_reminder(uid, int(a["reminder_id"]), a["field"], a["value"])
        return {"ok": True, **res} if res else {"xato": "Eslatma topilmadi yoki qiymat noto'g'ri"}
    if name == "complete_reminder":
        return {"ok": dbm.mark_reminder_done(uid, int(a["reminder_id"]))}
    if name == "delete_reminder":
        return {"ok": dbm.delete_reminder(uid, int(a["reminder_id"]))}

    # ---------------- yuk qidirish
    if name == "cargo_search":
        return cargo.start_search(uid, a["origin"], a.get("destination") or "", a.get("note") or "",
                                  a.get("hours"), a.get("mode") or "cargo")
    if name == "cargo_status":
        return cargo.status_report(uid)
    if name == "cargo_cancel":
        return cargo.cancel(uid, bool(a.get("stop_chats")))

    # ---------------- avto xabar
    if name == "autopost_status":
        return autopost.status(uid)
    if name == "autopost_control":
        act = a["action"]
        if act == "start":
            err = autopost.start(uid)
            ctx.show_ap = bool(err)
            return {"xato": err} if err else {"ok": True}
        if act == "stop":
            autopost.stop(uid)
            return {"ok": True}
        if act == "set_text":
            t = (a.get("text") or "").strip()
            if len(t) < 5:
                return {"xato": "Matn juda qisqa"}
            chk = await check_ad_text(t)
            if not chk.get("ok", True):
                return {"xato": f"Matn rad etildi: {chk.get('reason')}"}
            dbm.ap_update(uid, text=t[:3500])
            return {"ok": True, "maslahat": chk.get("tip")}
        if act == "set_interval":
            m = int(a.get("minutes") or 5)
            if m not in config.AP_INTERVALS:
                return {"xato": f"Mumkin bo'lgan qiymatlar: {config.AP_INTERVALS}"}
            dbm.ap_update(uid, interval_min=m)
            return {"ok": True}
        ctx.show_ap = True
        return {"ok": True, "izoh": "Avto xabar menyusi yuboriladi."}

    # ---------------- obuna, profil
    if name == "subscription_status":
        st, until = dbm.sub_state(ctx.user)
        price, days = dbm.setting_int("price", 100000), dbm.setting_int("period_days", 30)
        return {"holat": {"trial": "bepul sinov", "paid": "to'langan", "vip": "VIP (bepul)", "admin": "admin",
                          "expired": "tugagan"}.get(st, st),
                "tugash_sanasi": dbm.fmt_dt(until, True) if until else None,
                "narx": f"{core.money(price)} so'm / {days} kun"}
    if name == "open_payment":
        ctx.show_pay = True
        return {"ok": True, "izoh": "To'lov oynasi yuboriladi. Foydalanuvchiga 1 jumla yozing."}
    if name == "set_company_name":
        dbm.set_user(uid, company=a["name"].strip()[:80])
        return {"ok": True}

    if name == "show_table":
        kind = a["kind"]
        ctx.tables.update(["vehicle", "driver"] if kind == "both" else [kind])
        return {"ok": True, "izoh": "Jadval rasm ko'rinishida yuboriladi."}

    return {"xato": f"Noma'lum tool: {name}"}


def _dump(out) -> str:
    s = json.dumps(out, ensure_ascii=False, default=str)
    if len(s) > 20000:
        s = s[:20000] + '... (natija qisqartirildi)'
    return s


# ------------------------------------------------------------------ suhbat

COMMON_RULES = """
Umumiy qoidalar:
- Ma'lumotni FAQAT tool natijalaridan oling. O'zingizdan to'qimang. Topilmasa, "topilmadi" deng.
- Sanalarni KUN.OY.YIL ko'rinishida yozing va necha kun qolganini ham ayting.
- Markdown belgilaridan (**, #, |) foydalanmang. Oddiy matn va emoji: 🔴 (7 kun yoki o'tgan),
  🟡 (30 kungacha), 🟢 (30 dan ortiq), 🚛 fura, 👤 haydovchi, 📍 joy, 🛣 masofa.
- Asosiy til - o'zbek (lotin). Foydalanuvchi ruscha yozsa, ruscha javob bering.
- Aniq va to'liq yozing, ortiqcha gap qo'shmang. Logistikaga aloqasiz savolga qisqa javob bering.
- Telefon kodi, Telegram kodi, parol, karta PIN'ini HECH QACHON so'ramang."""


def _system_prompt(ctx: ChatContext) -> str:
    t = dbm.today()
    st, until = dbm.sub_state(ctx.user)
    if ctx.role == "driver":
        mid, d, vs = driver_scope(ctx.user)
        return f"""Siz "Logistika yordamchi" botisiz. Siz bilan HAYDOVCHI gaplashmoqda: {d['name'] if d else '?'}.
Bugungi sana: {t.strftime('%d.%m.%Y')}. Unga biriktirilgan furalar: {', '.join(v['plate'] for v in vs) or "yo'q"}.
Haydovchi faqat O'Z hujjatlarini va O'ZIGA biriktirilgan furaning hujjatlari/joylashuvini ko'ra oladi.
Boshqa furalar, boshqa haydovchilar yoki kompaniyaning boshqa ma'lumotlari haqida javob bermang.
Hujjat qo'shish/o'zgartirishni so'rasa - "buni menejeringiz qiladi" deng.
{COMMON_RULES}"""
    sub = {"trial": f"bepul sinov ({dbm.fmt_dt(until)} gacha)", "paid": f"to'langan ({dbm.fmt_dt(until)} gacha)",
           "vip": "VIP", "admin": "admin"}.get(st, st)
    admin = ("\nBu foydalanuvchi ADMIN: admin_* tool'lari orqali statistika va foydalanuvchilarni boshqara oladi."
             if dbm.is_admin(ctx.uid) else "")
    return f"""Siz "Logistika yordamchi" - yuk tashish (fura) kompaniyalari uchun Telegram botdagi aqlli yordamchisiz.
Bugungi sana: {t.strftime('%d.%m.%Y')}. Foydalanuvchi - MENEJER. Obunasi: {sub}.{admin}
Siz ulangan manbalar: (1) baza - furalar, haydovchilar, hujjatlar, eslatmalar; (2) GPS (Wialon) - joylashuv, km;
(3) yuk guruhlari - yuk/fura qidirish va e'lon egalari bilan avtomatik suhbat; (4) avto xabar - foydalanuvchi
akkaunti orqali guruhlarga e'lon yuborish.

Uslub: siz tajribali, xushmuomala logistika dispetcherisiz. Qisqa, aniq va ishonchli yozing, ortiqcha his-hayajon,
uzr va bo'sh gaplarsiz. Hech qachon "buni qila olmayman" demang - quyidagi tool'lar bilan deyarli hamma narsani
qila olasiz. Agar haqiqatan imkon bo'lmasa, qaysi tugma yoki bo'lim orqali qilish mumkinligini ayting.

Qoidalar:
1. Savolga javob berishdan oldin kerakli tool'ni chaqiring.
2. Faqat fura raqami yozilsa yoki "shu fura haqida" desa - vehicle_overview va BARCHA ma'lumotni bering.
3. "qayerda" -> gps_vehicle, "necha km yurdi" -> gps_mileage, "hamma mashina bugun" -> gps_fleet_mileage,
   "hammasi qayerda" -> gps_fleet. Oy/hafta so'ralsa sanalarni o'zingiz hisoblang. GPS ulanmagan bo'lsa gps_connect.
4. Jadval so'ralsa show_table. Matn bilan jadval chizmang.
5. O'zgartirish/qo'shish/o'chirishni DARHOL bajaring. Faqat butun furani yoki haydovchini o'chirishdan oldin
   bir marta "Tasdiqlaysizmi?" deb so'rang. Bajargach eski va yangi qiymatni ayting.
6. Haydovchi qo'shilganda telefonini ham so'rang - shu raqam bilan haydovchi botga kirsa, o'z hujjatlarini
   ko'radi va eslatmalar unga ham boradi.
7. "Toshkentdan Moskvaga yuk kerak" -> cargo_search(mode=cargo); "Andijondan Almatiga fura kerak" -> cargo_search(mode=truck).
   Keyin qisqa "🔎 Qidirilmoqda..." deb yozing - natijani bot o'zi yuboradi. Natijani to'qimang.
   Agar foydalanuvchi qayerDAN, qayerGA va nima (yuk yoki fura) kerakligini aniq yozmagan bo'lsa (masalan faqat
   "Toshkent Moskva") - cargo_search ni CHAQIRMANG, bunday so'rang: «Iltimos, to'liqroq yozing: Toshkentdan Moskvaga
   yuk kerak yoki Toshkentdan Moskvaga fura kerak».
   "Bekor qil", "to'xtat", "qidirmay qo'y" -> cargo_cancel; "gaplashishni ham to'xtat" -> cargo_cancel(stop_chats=true).
8. Avto xabar haqida so'rasa autopost_status / autopost_control. "To'xtat" -> stop, "yoq" -> start.
9. Obuna, to'lov haqida - subscription_status / open_payment.
10. Bir nechta amal so'ralsa hammasini bajaring.
{COMMON_RULES}"""


async def chat(user: dict, text: str):
    """(javob_matni, ChatContext) qaytaradi."""
    ctx = ChatContext(user)
    uid = ctx.uid
    if ctx.role == "driver":
        tools = DRIVER_TOOLS
    else:
        tools = MANAGER_TOOLS + (ADMIN_TOOLS if dbm.is_admin(uid) else [])
    dbm.save_message(uid, "user", text)
    messages = dbm.load_history(uid)
    # Prompt caching: tool'lar va system prompt keshlanadi -> takroriy so'rovlarda kirish narxi ancha arzon
    tools = tools[:-1] + [dict(tools[-1], cache_control={"type": "ephemeral"})]
    system = [{"type": "text", "text": _system_prompt(ctx), "cache_control": {"type": "ephemeral"}}]

    for _ in range(12):
        resp = await client.messages.create(
            model=config.CHAT_MODEL, max_tokens=2500, system=system, tools=tools, messages=messages)
        if resp.stop_reason != "tool_use":
            answer = "".join(b.text for b in resp.content if b.type == "text").strip()
            answer = answer or "Tushunmadim, savolni boshqacha yozib ko'ring."
            dbm.save_message(uid, "assistant", answer)
            return answer, ctx
        messages.append({"role": "assistant", "content": resp.content})
        calls = [b for b in resp.content if b.type == "tool_use"]
        outs = await asyncio.gather(*(run_tool(ctx, b.name, b.input) for b in calls))
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": b.id, "content": _dump(o)} for b, o in zip(calls, outs)]})

    answer = "Kechirasiz, so'rovni bajara olmadim. Savolni soddaroq yozib ko'ring."
    dbm.save_message(uid, "assistant", answer)
    return answer, ctx
