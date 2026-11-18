"""Claude AI: (1) hujjat rasmi/PDF dan ma'lumot olish, (2) baza + GPS ga ulangan suhbat (tool use)."""
import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from anthropic import AsyncAnthropic

import cargo
import config
import database as dbm
import gps
import userbot

client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY or "missing")

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


def _parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("AI javobida JSON topilmadi")
    return json.loads(text[start:end + 1])


async def extract_document(data: bytes, mime: str, hint: str = "") -> dict:
    b64 = base64.standard_b64encode(data).decode()
    if mime == "application/pdf":
        block = {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    else:
        block = {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
    resp = await client.messages.create(
        model=config.EXTRACT_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": [block, {"type": "text", "text": _extract_prompt(hint)}]}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(text)


# ====================================================================== 2) SUHBAT (TOOL USE)

@dataclass
class ChatContext:
    user_id: int
    tables: set = field(default_factory=set)  # "vehicle", "driver"
    login: bool = False    # userbotni ulash jarayonini boshlash kerak
    picker: bool = False   # guruh tanlash oynasini ko'rsatish kerak


def _t(name, desc, props=None, req=None):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props or {}, "required": req or []}}


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
DOC_KEYS = list(config.ALL_DOCS.keys())
DATE_HINT = "KUN.OY.YIL yoki YYYY-MM-DD; 'bugun' yoki 'kecha' ham bo'ladi"

TOOLS = [
    # ---------------- umumiy ko'rinish (baza + GPS birga)
    _t("vehicle_overview",
       "FURA HAQIDA TO'LIQ MA'LUMOT: bazadagi hujjatlari, haydovchisi, eslatmalari + GPS dan hozirgi joylashuvi "
       "(manzil), tezligi, bugungi va kechagi yurgan km, to'xtashlari. Foydalanuvchi fura raqamini yozsa yoki "
       "'shu fura haqida ma'lumot ber' desa DOIM shuni chaqiring. query - davlat raqami yoki qismi.",
       {"query": S}, ["query"]),

    # ---------------- GPS
    _t("gps_fleet", "Barcha mashinalarning hozirgi GPS holati: yurmoqda / to'xtagan / aloqa yo'q, tezlik, xarita."),
    _t("gps_vehicle",
       "Bitta mashina hozir qayerda: manzil, koordinata, tezlik, oxirgi signal, qurilma parametrlari.",
       {"query": S}, ["query"]),
    _t("gps_mileage",
       "Mashina qaysi kun(lar)da necha km yurgan, necha soat yurgan, qayerda to'xtagan. "
       "Bitta kun uchun date_from ni bering. Oraliq uchun date_to ni ham bering (eng ko'pi 31 kun). "
       "'Bu oy', 'o'tgan hafta' kabi so'rovlarda sanalarni o'zingiz hisoblab bering. "
       "detail=true bo'lsa bitta kun uchun to'xtashlar ro'yxati ham chiqadi.",
       {"query": S, "date_from": {"type": "string", "description": DATE_HINT},
        "date_to": {"type": "string", "description": DATE_HINT}, "detail": B}, ["query"]),
    _t("gps_fleet_mileage",
       "Butun park bo'yicha: shu kuni har bir mashina necha km yurgan (ko'pdan kamga), jami km. "
       "'Bugun kim eng ko'p yurdi', 'kecha hammasi necha km yurdi' kabi savollar uchun.",
       {"date": {"type": "string", "description": DATE_HINT}}),

    # ---------------- ro'yxatlar va qidiruv (baza)
    _t("list_vehicles", "Foydalanuvchining barcha furalari ro'yxati (haydovchisi bilan)."),
    _t("get_vehicle", "Bitta furaning bazadagi to'liq ma'lumoti (GPS siz): hujjatlari va muddatlari, "
                      "haydovchisi, eslatmalari. query - davlat raqami yoki uning qismi.", {"query": S}, ["query"]),
    _t("list_drivers", "Barcha haydovchilar ro'yxati."),
    _t("get_driver", "Bitta haydovchining hujjatlari va muddatlari. query - ism yoki ism qismi.",
       {"query": S}, ["query"]),
    _t("expiring_documents",
       "Muddati tugayotgan yoki o'tib ketgan hujjatlar, eng yaqini birinchi. 'Eng kam muddati qolgan fura' "
       "kabi savollar uchun within_days ni katta qiling (masalan 3650) va birinchi natijalarni oling.",
       {"within_days": I, "owner_type": {"type": "string", "enum": ["vehicle", "driver", "all"]}}),
    _t("documents_by_type",
       "Bitta hujjat turi bo'yicha HAMMA furalar/haydovchilar ro'yxati va muddatlari (masalan barcha "
       "tahograflar, barcha sug'urtalar). Hujjati yo'qlar alohida chiqadi.",
       {"doc_type": {"type": "string", "enum": DOC_KEYS}}, ["doc_type"]),
    _t("missing_documents",
       "Qaysi fura yoki haydovchida qaysi hujjatlar umuman kiritilmagan (yo'q).",
       {"owner_type": {"type": "string", "enum": ["vehicle", "driver"]}}, ["owner_type"]),

    # ---------------- furalar / haydovchilar: qo'shish, o'zgartirish, o'chirish
    _t("add_vehicle", "Yangi fura qo'shish.", {"plate": S, "model": S}, ["plate"]),
    _t("update_vehicle", "Furaning raqamini (plate), modelini (model) yoki VIN ini o'zgartirish.",
       {"query": S, "field": {"type": "string", "enum": ["plate", "model", "vin"]}, "value": S},
       ["query", "field", "value"]),
    _t("delete_vehicle",
       "Furani butunlay o'chirish (hujjatlari va eslatmalari bilan). FAQAT foydalanuvchi tasdiqlagandan keyin, "
       "confirm=true bilan.", {"query": S, "confirm": B}, ["query", "confirm"]),
    _t("add_driver", "Yangi haydovchi qo'shish.", {"name": S, "phone": S}, ["name"]),
    _t("update_driver", "Haydovchining ismi (name) yoki telefonini (phone) o'zgartirish.",
       {"query": S, "field": {"type": "string", "enum": ["name", "phone"]}, "value": S},
       ["query", "field", "value"]),
    _t("delete_driver",
       "Haydovchini butunlay o'chirish (hujjatlari bilan). FAQAT foydalanuvchi tasdiqlagandan keyin, "
       "confirm=true bilan.", {"query": S, "confirm": B}, ["query", "confirm"]),
    _t("assign_driver", "Haydovchini furaga biriktirish.", {"driver_name": S, "plate": S},
       ["driver_name", "plate"]),
    _t("unassign_driver", "Furadan haydovchini ajratish (fura haydovchisiz qoladi).", {"plate": S}, ["plate"]),

    # ---------------- hujjatlar
    _t("save_document", "Hujjatni qo'lda qo'shish yoki mavjudini yangilash. Sanalar KUN.OY.YIL yoki YYYY-MM-DD.",
       {"owner_type": {"type": "string", "enum": ["vehicle", "driver"]},
        "owner": {"type": "string", "description": "fura raqami yoki haydovchi ismi"},
        "doc_type": {"type": "string", "enum": DOC_KEYS},
        "doc_number": S, "issue_date": S, "expiry_date": S, "model": S},
       ["owner_type", "owner", "doc_type"]),
    _t("update_document", "Mavjud hujjatning bitta maydonini o'zgartirish (faqat foydalanuvchi so'raganda).",
       {"doc_id": I,
        "field": {"type": "string", "enum": ["doc_number", "issue_date", "expiry_date", "issuer", "notes"]},
        "value": S}, ["doc_id", "field", "value"]),
    _t("delete_document",
       "Hujjatni o'chirish (faqat foydalanuvchi aniq so'raganda). Yoki doc_id bering, yoki "
       "owner_type + owner + doc_type bering (masalan '01140WLA ning tahograf hujjatini o'chir').",
       {"doc_id": I, "owner_type": {"type": "string", "enum": ["vehicle", "driver"]},
        "owner": S, "doc_type": {"type": "string", "enum": DOC_KEYS}}),

    # ---------------- eslatmalar
    _t("set_china_queue_date",
       "Furaning Xitoydan olingan eng oxirgi navbat sanasini yozish. Bu avtomatik ikkita eslatma yaratadi: "
       "Xitoyga kirish navbati (shu sanadan) va Rossiyaga chiqish navbati (3 kun keyin).",
       {"plate": S, "date": S}, ["plate", "date"]),
    _t("add_reminder",
       "Qo'lda sanali eslatma qo'shish. kind: uz_entry (O'zbekistonga kirish navbati), "
       "debt (qarzdor kompaniya to'lovi), custom (boshqa vazifa).",
       {"kind": {"type": "string", "enum": ["uz_entry", "debt", "custom"]},
        "title": {"type": "string", "description": "qisqa tavsif (kompaniya nomi, vazifa matni)"},
        "date": S, "plate": {"type": "string", "description": "fura raqami (ixtiyoriy)"}},
       ["kind", "date"]),
    _t("list_reminders", "Bajarilmagan eslatmalar ro'yxati."),
    _t("update_reminder", "Eslatma sanasini (remind_date) yoki matnini (title) o'zgartirish.",
       {"reminder_id": I, "field": {"type": "string", "enum": ["remind_date", "title"]}, "value": S},
       ["reminder_id", "field", "value"]),
    _t("complete_reminder", "Eslatmani bajarilgan deb belgilash.", {"reminder_id": I}, ["reminder_id"]),
    _t("delete_reminder", "Eslatmani butunlay o'chirish.", {"reminder_id": I}, ["reminder_id"]),

    # ---------------- userbot va yuk qidirish
    _t("cargo_search",
       "YUK QIDIRISH. Foydalanuvchi 'Toshkentdan Moskvaga yuk kerak' kabi yozsa shuni chaqiring: userbot ulangan "
       "guruhlarning oxirgi soatlardagi xabarlarini o'qib, yo'nalishga mos YUK EGALARINI topadi va ularga lichkada "
       "yozib suhbat boshlaydi. Natijani bot keyin o'zi yuboradi (bu tool darhol qaytadi). origin - qayerdan, "
       "destination - qayerga (bo'lmasa bo'sh), note - narx chegarasi, yuk turi kabi qo'shimcha shartlar.",
       {"origin": S, "destination": S, "note": S, "hours": {"type": "integer", "description": "necha soat orqaga (odatda 24)"}},
       ["origin"]),
    _t("cargo_status", "Yuk qidiruvlari va yuk egalari bilan suhbatlar holati: kimga yozilgan, kim javob berdi, "
                       "qaysi biri menejerni kutmoqda, kunlik limit."),
    _t("userbot_status", "Userbot (shaxsiy akkaunt) ulanganmi va qaysi guruhlar kuzatilmoqda."),
    _t("userbot_connect", "Userbot akkauntini ulashni boshlash. Telefon raqam va kodni bot o'zi alohida so'raydi - "
                          "siz faqat qisqa javob bering."),
    _t("userbot_groups", "Kuzatiladigan guruhlarni tanlash oynasini ochish."),

    # ---------------- jadval
    _t("show_table",
       "Rangli jadvalni (rasm) foydalanuvchiga yuborish: qizil/sariq/yashil. "
       "Jadval so'ralganda DOIM shuni chaqiring, matn bilan jadval chizmang.",
       {"kind": {"type": "string", "enum": ["vehicle", "driver", "both"]}}, ["kind"]),
]

STATUS_UZ = {"missing": "yo'q", "noexp": "muddatsiz", "expired": "muddati o'tgan",
             "red": "7 kundan kam", "yellow": "30 kundan kam", "green": "yaxshi"}


# ------------------------------------------------------------------ yordamchilar

def _gdate(s):
    """'bugun' / 'kecha' / KUN.OY.YIL / YYYY-MM-DD -> date. Noto'g'ri bo'lsa None."""
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
                "fayl_saqlangan": bool(r and r.get("file_id")),
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


async def _find_gps(query):
    """(mashina, barcha mashinalar). Topilmasa mashina = None."""
    units = await gps.get_units()
    return gps.find_unit(units, query), units


async def _safe(coro):
    """GPS qismi xato bersa ham, qolgan javob buzilmasin."""
    try:
        return await coro
    except Exception as e:
        logging.warning("GPS xatosi: %s", e)
        return {"xato": f"GPS: {e}"}


async def _vehicle_overview(uid, query):
    v = dbm.find_vehicle(uid, query)
    out = {"baza": _vehicle_info(uid, v) if v else {"xato": "Bazada bunday fura topilmadi"}}
    plate = v["plate"] if v else query
    try:
        u, _ = await _find_gps(plate)
    except Exception as e:
        out["gps"] = {"xato": f"GPS tizimiga ulanib bo'lmadi: {e}"}
        return out
    if not u:
        out["gps"] = {"xato": "GPS tizimida bunday mashina topilmadi (Wialon'dagi nomda raqam bo'lishi kerak)"}
        return out
    today = dbm.today()
    now, bugun, kecha = await asyncio.gather(
        _safe(gps.unit_summary(u)),
        _safe(gps.day_report(u, today, detail=True)),
        _safe(gps.day_report(u, today - timedelta(days=1), detail=True)))
    out["gps"] = {"hozir": now, "bugun": bugun, "kecha": kecha}
    return out


def _missing(uid, owner_type):
    if owner_type == "vehicle":
        owners, labels = dbm.list_vehicles(uid), config.VEHICLE_DOCS
        name = lambda o: o["plate"]
    else:
        owners, labels = dbm.list_drivers(uid), config.DRIVER_DOCS
        name = lambda o: o["name"]
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


# ------------------------------------------------------------------ asboblarni bajarish

async def run_tool(ctx: ChatContext, name: str, a: dict):
    uid = ctx.user_id
    try:
        # ---------------- GPS
        if name == "vehicle_overview":
            return await _vehicle_overview(uid, a["query"])

        if name == "gps_fleet":
            units = await gps.get_units()
            return [{"gps_nomi": u["name"], "holat": gps.status_word(u), "tezlik_km_soat": u["speed"],
                     "oxirgi_signal": gps._ago(u),
                     "xarita": gps.maps_link(u) if u["lat"] is not None else None} for u in units]

        if name == "gps_vehicle":
            u, _ = await _find_gps(a["query"])
            if not u:
                return {"xato": "GPS tizimida bunday mashina topilmadi"}
            return await gps.unit_summary(u)

        if name == "gps_mileage":
            u, _ = await _find_gps(a["query"])
            if not u:
                return {"xato": "GPS tizimida bunday mashina topilmadi"}
            d1 = _gdate(a.get("date_from"))
            d2 = _gdate(a.get("date_to")) if a.get("date_to") else d1
            if not d1 or not d2:
                return {"xato": "Sana noto'g'ri. KUN.OY.YIL formatida yozing."}
            if d2 > dbm.today():
                d2 = dbm.today()
            try:
                return await gps.period_report(u, d1, d2, detail=bool(a.get("detail")))
            except ValueError as e:
                return {"xato": str(e)}

        if name == "gps_fleet_mileage":
            d = _gdate(a.get("date"))
            if not d:
                return {"xato": "Sana noto'g'ri"}
            return await gps.fleet_mileage(await gps.get_units(), d)

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
            res = []
            for d in dbm.list_drivers(uid):
                res.append({"id": d["id"], "ism": d["name"], "telefon": d["phone"],
                            "furalari": [v["plate"] for v in dbm.vehicles_of_driver(uid, d["id"])]})
            return res

        if name == "get_driver":
            d = dbm.find_driver(uid, a["query"])
            if not d:
                return {"xato": "Bunday haydovchi topilmadi"}
            return {"id": d["id"], "ism": d["name"], "telefon": d["phone"],
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
            return {"ok": bool(did), "haydovchi_id": did}

        if name == "update_driver":
            d = dbm.find_driver(uid, a["query"])
            if not d:
                return {"xato": "Bunday haydovchi topilmadi"}
            res = dbm.update_driver(uid, d["id"], a["field"], a["value"])
            if res and res.get("error"):
                return {"xato": res["error"]}
            return {"ok": True, "haydovchi": d["name"], **(res or {})}

        if name == "delete_driver":
            d = dbm.find_driver(uid, a["query"])
            if not d:
                return {"xato": "Bunday haydovchi topilmadi"}
            if not a.get("confirm"):
                return {"xato": f"{d['name']} ni o'chirishdan oldin foydalanuvchidan tasdiq so'rang."}
            res = dbm.delete_driver(uid, d["id"])
            return {"ok": bool(res), "ochirildi": d["name"], **(res or {})}

        if name == "assign_driver":
            d = dbm.find_driver(uid, a["driver_name"])
            v = dbm.find_vehicle(uid, a["plate"])
            if not d or not v:
                return {"xato": "Haydovchi yoki fura topilmadi. Avval qo'shing."}
            dbm.assign_driver(uid, d["id"], v["id"])
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
            # Mavjud hujjat bo'lsa - uni almashtirmaymiz (saqlangan rasm/PDF yo'qolmasin), maydonlarni yangilaymiz
            existing = dbm.owner_docs(uid, ot, oid).get(dt)
            if existing and dt not in config.MULTI_DOC_TYPES:
                for fld, val in (("doc_number", a.get("doc_number")), ("issue_date", iss), ("expiry_date", exp)):
                    if val:
                        dbm.update_document(uid, existing["id"], fld, val)
                return {"ok": True, "doc_id": existing["id"], "yangilandi": True}
            did = dbm.save_document(uid, ot, oid, dt, a.get("doc_number"), iss, exp)
            return {"ok": True, "doc_id": did}

        if name == "update_document":
            res = dbm.update_document(uid, int(a["doc_id"]), a["field"], a["value"])
            return {"ok": True, **res} if res else {"xato": "Hujjat topilmadi yoki qiymat noto'g'ri"}

        if name == "delete_document":
            if a.get("doc_id"):
                doc = dbm.get_document(uid, int(a["doc_id"]))
                if not doc:
                    return {"xato": "Hujjat topilmadi"}
                ok = dbm.delete_document(uid, int(a["doc_id"]))
                return {"ok": ok, "ochirildi": config.ALL_DOCS.get(doc["doc_type"], doc["doc_type"]),
                        "eski_tugash_sanasi": dbm.fmt_date(doc["expiry_date"]) if doc["expiry_date"] else None}
            if not (a.get("owner_type") and a.get("owner") and a.get("doc_type")):
                return {"xato": "doc_id yoki (owner_type, owner, doc_type) kerak"}
            o = _owner_of(uid, a["owner_type"], a["owner"])
            if not o:
                return {"xato": "Egasi topilmadi"}
            n = dbm.delete_documents_of_type(uid, a["owner_type"], o["id"], a["doc_type"])
            return {"ok": n > 0, "ochirilgan_soni": n,
                    "hujjat": config.ALL_DOCS.get(a["doc_type"], a["doc_type"])}

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
                 "sana": dbm.fmt_date((date.fromisoformat(d) + timedelta(
                     days=config.RUSSIA_AFTER_CHINA_DAYS)).isoformat())}]}

        if name == "add_reminder":
            d = dbm.parse_date(a["date"])
            if not d:
                return {"xato": "Sana noto'g'ri"}
            vid = None
            if a.get("plate"):
                v = dbm.find_vehicle(uid, a["plate"])
                vid = v["id"] if v else None
            rid = dbm.add_reminder(uid, a["kind"], a.get("title"), d, vid, pre_days=1)
            return {"ok": True, "eslatma_id": rid}

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

        # ---------------- userbot va yuk qidirish (faqat adminlar)
        if name in ("cargo_search", "cargo_status", "userbot_status", "userbot_connect", "userbot_groups"):
            if not config.is_admin(uid):
                return {"xato": "Bu imkoniyat faqat adminlar uchun (.env da ALLOWED_USERS to'ldirilishi kerak)."}

        if name == "cargo_search":
            return cargo.start_search(uid, a["origin"], a.get("destination") or "", a.get("note") or "",
                                      a.get("hours"))

        if name == "cargo_status":
            return cargo.status_report(uid)

        if name == "userbot_status":
            return {"ulangan": userbot.is_connected(uid),
                    "guruhlar": [g["title"] for g in dbm.ub_active_groups(uid)]}

        if name == "userbot_connect":
            ctx.login = True
            return {"ok": True, "izoh": "Bot o'zi telefon raqamni so'raydi. Foydalanuvchiga faqat 1 jumla yozing."}

        if name == "userbot_groups":
            if not userbot.is_connected(uid):
                return {"xato": "Avval userbotni ulang."}
            ctx.picker = True
            return {"ok": True, "izoh": "Guruh tanlash oynasi yuboriladi."}

        if name == "show_table":
            kind = a["kind"]
            ctx.tables.update(["vehicle", "driver"] if kind == "both" else [kind])
            return {"ok": True, "izoh": "Jadval rasm ko'rinishida yuboriladi."}

        return {"xato": f"Noma'lum tool: {name}"}
    except Exception as e:  # noqa
        logging.exception("tool xatosi: %s", name)
        return {"xato": str(e)}


def _dump(out) -> str:
    s = json.dumps(out, ensure_ascii=False)
    if len(s) > 20000:
        s = s[:20000] + '... (natija qisqartirildi)'
    return s


# ------------------------------------------------------------------ suhbat

def _system_prompt() -> str:
    t = dbm.today()
    return f"""Siz "Logistika yordamchi" - yuk tashish (fura) kompaniyalari uchun Telegram botdagi yordamchisiz.
Bugungi sana: {t.strftime('%d.%m.%Y')}.
Siz ikki manbaga ulangansiz: (1) baza - fura va haydovchilar hujjatlari (texpasport, sug'urta, tahograf, prava, viza...),
(2) GPS (Wialon) - mashinalarning joylashuvi, tezligi, yurgan masofasi.

Qoidalar:
1. Ma'lumotni FAQAT tool natijalaridan oling. O'zingizdan to'qimang. Topilmasa, "topilmadi" deng.
2. Savolga javob berishdan oldin kerakli tool'ni chaqiring. Masalan, "eng kam muddati qolgan fura" -> expiring_documents.
3. Foydalanuvchi faqat fura raqamini yozsa (masalan 01140WLA) yoki "shu fura haqida ma'lumot ber" desa,
   vehicle_overview ni chaqiring va BARCHA ma'lumotni bering: model, haydovchi, hujjatlar muddati (qizil/sariq
   ko'rsatib), hozirgi joylashuv (manzil + xarita havolasi), tezlik, holat, bugun va kecha necha km yurgani,
   yurgan vaqti, to'xtashlari, eslatmalar.
4. Joylashuv/yurish savollari: "qayerda" -> gps_vehicle, "necha km yurdi" -> gps_mileage (bugun/kecha/oraliq),
   "hamma mashina bugun" -> gps_fleet_mileage, "hammasi qayerda" -> gps_fleet. Oy yoki hafta so'ralsa sanalarni
   o'zingiz hisoblab date_from va date_to bering. Km GPS nuqtalaridan hisoblanadi (taxminiy) - zarur bo'lsa shuni ayting.
5. Jadval so'ralsa show_table ni chaqiring va qisqa izoh yozing. Matn bilan jadval chizmang.
6. O'zgartirish, qo'shish yoki o'chirishni foydalanuvchi so'raganda DARHOL bajaring, ortiqcha savol bermang.
   Faqat butun furani yoki haydovchini o'chirishdan oldin bir marta "Tasdiqlaysizmi?" deb so'rang
   (hujjatlari ham o'chadi), "ha" desa confirm=true bilan chaqiring. Bajargach eski va yangi qiymatni ayting.
7. Bir nechta fura/hujjat so'ralsa, hammasini bajaring. Ish tugamaguncha tool'larni chaqirishda davom eting.
8. Sanalarni KUN.OY.YIL ko'rinishida yozing va necha kun qolganini ham ayting.
9. Markdown belgilaridan (**, #, |) foydalanmang. Oddiy matn va emoji ishlating: 🔴 (7 kun yoki o'tgan),
   🟡 (30 kungacha), 🟢 (30 dan ortiq), 🚛 fura, 👤 haydovchi, 📍 joy, 🛣 masofa.
10. Asosiy til - o'zbek (lotin). Foydalanuvchi ruscha yozsa, ruscha javob bering.
11. Aniq va to'liq yozing, ortiqcha gap qo'shmang. Logistikaga aloqasiz savolga qisqa javob bering va asosiy ishga qaytaring.
12. Siz userbot (foydalanuvchining shaxsiy Telegram akkaunti) orqali yuk guruhlarini o'qiy olasiz.
    «Toshkentdan Moskvaga yuk kerak» yoki shunga o'xshash yuk topish so'rovi kelsa - cargo_search ni chaqiring
    (origin=Toshkent, destination=Moskva; narx chegarasi va boshqa shartlarni note ga yozing) va faqat qisqa javob bering:
    «Qidirilmoqda...» (guruhlar tekshirilib, yuk egalariga yozilgach bot o'zi xabar beradi). Natijani o'zingizdan to'qimang.
    Userbot ulanmagan bo'lsa tool xato qaytaradi - foydalanuvchiga userbot_connect ni taklif qiling.
    «Userbotni ulayman / akkauntni ulayman» desa - userbot_connect; «guruhlarni tanlayman» desa - userbot_groups.
    Telefon raqam, kod va parolni HECH QACHON suhbatda so'ramang - ularni bot alohida, xavfsiz so'raydi.
    «Yuk egalari bilan nima bo'ldi» kabi savollarga cargo_status ni chaqiring."""


async def chat(user_id: int, text: str):
    """Foydalanuvchi xabariga javob beradi. (javob_matni, ChatContext) qaytaradi."""
    ctx = ChatContext(user_id)
    dbm.save_message(user_id, "user", text)
    messages = dbm.load_history(user_id)
    system = _system_prompt()

    for _ in range(12):  # tool ishlatish sikli (cheklangan)
        resp = await client.messages.create(
            model=config.CHAT_MODEL, max_tokens=2500, system=system, tools=TOOLS, messages=messages)

        if resp.stop_reason != "tool_use":
            answer = "".join(b.text for b in resp.content if b.type == "text").strip()
            answer = answer or "Tushunmadim, savolni boshqacha yozib ko'ring."
            dbm.save_message(user_id, "assistant", answer)
            return answer, ctx

        messages.append({"role": "assistant", "content": resp.content})
        calls = [b for b in resp.content if b.type == "tool_use"]
        outs = await asyncio.gather(*(run_tool(ctx, b.name, b.input) for b in calls))
        results = [{"type": "tool_result", "tool_use_id": b.id, "content": _dump(o)}
                   for b, o in zip(calls, outs)]
        messages.append({"role": "user", "content": results})

    answer = "Kechirasiz, so'rovni bajara olmadim. Savolni soddaroq yozib ko'ring."
    dbm.save_message(user_id, "assistant", answer)
    return answer, ctx
