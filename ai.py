"""Claude AI: (1) hujjat rasmi/PDF dan ma'lumot olish, (2) bazaga ulangan suhbat (tool use)."""
import base64
import json
import logging
from datetime import date, timedelta
from dataclasses import dataclass, field

from anthropic import AsyncAnthropic

import config
import database as dbm

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


def _t(name, desc, props=None, req=None):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props or {}, "required": req or []}}


S = {"type": "string"}
I = {"type": "integer"}
DOC_KEYS = list(config.ALL_DOCS.keys())

TOOLS = [
    _t("list_vehicles", "Foydalanuvchining barcha furalari ro'yxati."),
    _t("get_vehicle", "Bitta furaning to'liq ma'lumoti: hujjatlari va muddatlari, haydovchisi, eslatmalari. "
                      "query - davlat raqami yoki uning qismi.", {"query": S}, ["query"]),
    _t("list_drivers", "Barcha haydovchilar ro'yxati."),
    _t("get_driver", "Bitta haydovchining hujjatlari va muddatlari. query - ism yoki ism qismi.",
       {"query": S}, ["query"]),
    _t("expiring_documents",
       "Muddati tugayotgan yoki o'tib ketgan hujjatlar, eng yaqini birinchi. 'Eng kam muddati qolgan fura' "
       "kabi savollar uchun within_days ni katta qiling (masalan 3650) va birinchi natijalarni oling.",
       {"within_days": I, "owner_type": {"type": "string", "enum": ["vehicle", "driver", "all"]}}),
    _t("add_vehicle", "Yangi fura qo'shish.", {"plate": S, "model": S}, ["plate"]),
    _t("add_driver", "Yangi haydovchi qo'shish.", {"name": S, "phone": S}, ["name"]),
    _t("assign_driver", "Haydovchini furaga biriktirish.", {"driver_name": S, "plate": S},
       ["driver_name", "plate"]),
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
    _t("delete_document", "Hujjatni o'chirish (faqat foydalanuvchi aniq so'raganda).", {"doc_id": I}, ["doc_id"]),
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
    _t("complete_reminder", "Eslatmani bajarilgan deb belgilash.", {"reminder_id": I}, ["reminder_id"]),
    _t("show_table",
       "Rangli jadvalni (rasm) foydalanuvchiga yuborish: qizil/sariq/yashil. "
       "Jadval so'ralganda DOIM shuni chaqiring, matn bilan jadval chizmang.",
       {"kind": {"type": "string", "enum": ["vehicle", "driver", "both"]}}, ["kind"]),
]

STATUS_UZ = {"missing": "yo'q", "noexp": "muddatsiz", "expired": "muddati o'tgan",
             "red": "7 kundan kam", "yellow": "30 kundan kam", "green": "yaxshi"}


def _doc_list(uid, owner_type, owner_id, labels):
    docs = dbm.owner_docs(uid, owner_type, owner_id)
    out = []
    for key, label in labels.items():
        r = docs.get(key)
        st, d = dbm.doc_status(r)
        out.append({
            "doc_id": r["id"] if r else None, "hujjat": label,
            "raqam": r["doc_number"] if r else None,
            "tugash_sanasi": dbm.fmt_date(r["expiry_date"]) if r and r["expiry_date"] else None,
            "qolgan_kun": d, "holat": STATUS_UZ[st],
        })
    return out


def _vehicle_info(uid, v):
    drv = dbm.get_driver(uid, v["driver_id"]) if v["driver_id"] else None
    rems = [{"id": r["id"], "eslatma": dbm.reminder_title(r), "sana": dbm.fmt_date(r["remind_date"])}
            for r in dbm.list_reminders(uid) if r["vehicle_id"] == v["id"]]
    return {"id": v["id"], "raqam": v["plate"], "model": v["model"], "vin": v["vin"],
            "haydovchi": drv["name"] if drv else None,
            "xitoy_navbat_sanasi": dbm.fmt_date(v["china_queue_date"]) if v["china_queue_date"] else None,
            "hujjatlar": _doc_list(uid, "vehicle", v["id"], config.VEHICLE_DOCS), "eslatmalar": rems}


def run_tool(ctx: ChatContext, name: str, a: dict):
    uid = ctx.user_id
    try:
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
                     "qolgan_kun": r["days_left"]} for r in rows[:40]]

        if name == "add_vehicle":
            vid = dbm.get_or_create_vehicle(uid, a["plate"], a.get("model"))
            return {"ok": bool(vid), "fura_id": vid}

        if name == "add_driver":
            did = dbm.get_or_create_driver(uid, a["name"], a.get("phone"))
            return {"ok": bool(did), "haydovchi_id": did}

        if name == "assign_driver":
            d = dbm.find_driver(uid, a["driver_name"])
            v = dbm.find_vehicle(uid, a["plate"])
            if not d or not v:
                return {"xato": "Haydovchi yoki fura topilmadi. Avval qo'shing."}
            dbm.assign_driver(uid, d["id"], v["id"])
            return {"ok": True, "haydovchi": d["name"], "fura": v["plate"]}

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
            did = dbm.save_document(uid, ot, oid, dt, a.get("doc_number"), iss, exp)
            return {"ok": True, "doc_id": did}

        if name == "update_document":
            res = dbm.update_document(uid, int(a["doc_id"]), a["field"], a["value"])
            return {"ok": True, **res} if res else {"xato": "Hujjat topilmadi yoki qiymat noto'g'ri"}

        if name == "delete_document":
            return {"ok": dbm.delete_document(uid, int(a["doc_id"]))}

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

        if name == "complete_reminder":
            return {"ok": dbm.mark_reminder_done(uid, int(a["reminder_id"]))}

        if name == "show_table":
            kind = a["kind"]
            ctx.tables.update(["vehicle", "driver"] if kind == "both" else [kind])
            return {"ok": True, "izoh": "Jadval rasm ko'rinishida yuboriladi."}

        return {"xato": f"Noma'lum tool: {name}"}
    except Exception as e:  # noqa
        logging.exception("tool xatosi: %s", name)
        return {"xato": str(e)}


def _system_prompt() -> str:
    t = dbm.today()
    return f"""Siz "Logistika yordamchi" - yuk tashish (fura) kompaniyalari uchun Telegram botdagi yordamchisiz.
Bugungi sana: {t.strftime('%d.%m.%Y')}.
Foydalanuvchi fura va haydovchilarining hujjatlarini (texpasport, sug'urta, tahograf, prava, viza va h.k.) bazada saqlaydi.

Qoidalar:
1. Ma'lumotni FAQAT tool natijalaridan oling. O'zingizdan to'qimang. Topilmasa, "topilmadi" deng.
2. Savolga javob berishdan oldin kerakli tool'ni chaqiring. Masalan, "eng kam muddati qolgan fura" -> expiring_documents.
3. Jadval so'ralsa show_table ni chaqiring va qisqa izoh yozing. Matn bilan jadval chizmang.
4. O'zgartirish yoki o'chirishni faqat foydalanuvchi aniq so'raganda bajaring. Keyin eski va yangi qiymatni ayting.
5. Sanalarni KUN.OY.YIL ko'rinishida yozing va necha kun qolganini ham ayting.
6. Markdown belgilaridan (**, #, |) foydalanmang. Oddiy matn va emoji ishlating: 🔴 (7 kun yoki o'tgan), 🟡 (30 kungacha), 🟢 (30 dan ortiq).
7. Asosiy til - o'zbek (lotin). Foydalanuvchi ruscha yozsa, ruscha javob bering.
8. Qisqa va aniq yozing. Logistikaga aloqasiz savolga qisqa javob bering va asosiy ishga qaytaring."""


async def chat(user_id: int, text: str):
    """Foydalanuvchi xabariga javob beradi. (javob_matni, ChatContext) qaytaradi."""
    ctx = ChatContext(user_id)
    dbm.save_message(user_id, "user", text)
    messages = dbm.load_history(user_id)
    system = _system_prompt()

    for _ in range(8):  # tool ishlatish sikli (cheklangan)
        resp = await client.messages.create(
            model=config.CHAT_MODEL, max_tokens=1500, system=system, tools=TOOLS, messages=messages)

        if resp.stop_reason != "tool_use":
            answer = "".join(b.text for b in resp.content if b.type == "text").strip()
            answer = answer or "Tushunmadim, savolni boshqacha yozib ko'ring."
            dbm.save_message(user_id, "assistant", answer)
            return answer, ctx

        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                out = run_tool(ctx, b.name, b.input)
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, ensure_ascii=False)})
        messages.append({"role": "user", "content": results})

    answer = "Kechirasiz, so'rovni bajara olmadim. Savolni soddaroq yozib ko'ring."
    dbm.save_message(user_id, "assistant", answer)
    return answer, ctx
