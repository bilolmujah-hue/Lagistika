"""Hujjat rasmiga yozilgan IZOHNI tushunish (AI siz, tez va aniq).

Foydalanuvchi rasmga «019314BA техпаспорт орка тарафи», «01928WNA sug'urta Qozog'iston», «Aliyev Vali prava»
kabi izoh yozadi. Bu yerdan:
  • fura raqami  - avval foydalanuvchining bazadagi furalari bilan solishtiriladi, keyin raqam shabloni bo'yicha;
  • haydovchi    - bazadagi haydovchilar ismi bilan solishtiriladi;
  • hujjat turi  - o'zbek (lotin/kirill), rus va ingliz kalit so'zlari bo'yicha.
Izoh - foydalanuvchining aniq ko'rsatmasi, shuning uchun AI natijasidan USTUN turadi.
"""
import re
import time

import database as dbm

# (shablon, hujjat turi) - TARTIB MUHIM: aniqrog'i birinchi (masalan «техпаспорт» «паспорт» dan oldin)
_DOC_RULES = [
    (r"tex\s*pas|tex\s*pass|техпас|тех\s*пас|техник\s*паспорт|texnik\s*pasport|qayd\s*etish\s*guvoh|"
     r"свидетельств\w*\s*о\s*регистрац|registration\s*cert|\bstc\b", "texpasport"),
    (r"chip|чип|карт\w*\s*тахограф|tahograf\s*kart|taxograf\s*kart|тахограф\w*\s*карт|driver\s*card", "chip_karta"),
    (r"tahograf|taxograf|тахограф|tachograph", "tahograf"),
    (r"tex\s*ko'?rik|tex\s*korik|texosmotr|техосмотр|тех\s*осмотр|техкўрик|техкорик|tex\s*osmotr|ko'?rikdan", "texkorik"),
    (r"dopusk|допуск|допущ", "dopusk"),
    (r"litsenz|licenz|лиценз|license|licence", "litsenziya"),
    (r"\btir\b|\bтир\b|carnet|карнет", "tir"),
    (r"dazvol|дозвол|razreshen|разрешени|ruxsatnoma|рухсатнома|permit", "dazvol"),
    (r"yashil\s*kart|ящил|яшил|зел[её]н\w*\s*карт|green\s*card|xitoy\s*sug", "yashil_karta"),
    (r"sug'?urta|sugʻurta|сугурта|суғурта|страхов|insurance|\bpolis\b|\bполис\b|osago|осаго", "sugurta"),
    (r"zagran|загран|xorijiy\s*pasport|хорижий|international\s*passport", "zagranpasport"),
    (r"shengen|шенген|schengen|\bviza\b|\bвиза\b|\bvisa\b", "shengen"),
    (r"kod\s*95|код\s*95|code\s*95|cod\s*95", "kod95"),
    (r"\badr\b|\bадр\b|допог", "adr"),
    (r"plastik|пластик|bank\s*kart|банковск|humo|uzcard|visa\s*card", "plastik_karta"),
    (r"prava|права|haydovchilik|ҳайдовчилик|водительск|driver'?s?\s*licen|guvohnoma|гувоҳнома", "prava"),
    (r"pasport|паспорт|passport|id\s*kart|id-?карт|айди", "milliy_pasport"),
]
_COUNTRY = [
    (r"qozog|qozoq|казах|қозоғ|қозоқ|\bkz\b|\bкз\b|kazakh", "sugurta_kz"),
    (r"rossiya|росси|\bru\b|\bрф\b|russia|\brus\b", "sugurta_ru"),
    (r"o'?zbek|узбек|ўзбек|\buz\b|uzbek", "sugurta_uz"),
]
_SUFFIXES = r"(?:ning|niki|ni|ga|da|dan|ники|нинг|ни|га|да|дан)?"
_CYR2LAT = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")
# O'zbekiston raqamlari: 01A123BC, 01123ABC, 019314BA (tirkama), 01928WNA; xorijiy: AB1234CD, A123BC77
_PLATE_RES = [
    re.compile(r"(?<![A-Z0-9])(\d{2}\s?[A-Z]?\s?\d{3,4}\s?[A-Z]{1,3})(?![A-Z0-9])"),
    re.compile(r"(?<![A-Z0-9])([A-Z]{1,3}\s?\d{3,4}\s?[A-Z]{1,3}(?:\s?\d{2,3})?)(?![A-Z0-9])"),
]

LAST_PLATE: dict = {}   # uid -> (raqam, vaqt): albomdagi keyingi rasmlar va tez tanlash uchun
GROUP_CAPTION: dict = {}  # media_group_id -> (izoh, vaqt): albomda izoh faqat birinchi rasmda bo'ladi


def _low(s: str) -> str:
    return (s or "").lower().replace("ʻ", "'").replace("’", "'").replace("`", "'").replace("ʼ", "'").replace("ё", "е")


def doc_type_of(caption: str, ai_type: str = None):
    """Izohdagi hujjat turi (config kaliti) yoki None."""
    t = _low(caption)
    if not t:
        return None
    for pat, key in _DOC_RULES:
        if re.search(pat, t):
            if key != "sugurta":
                return key
            for cpat, ckey in _COUNTRY:
                if re.search(cpat, t):
                    return ckey
            # davlat yozilmagan: AI aniqlagan sug'urta turi bo'lsa - o'shani, bo'lmasa O'zbekiston
            return ai_type if (ai_type or "").startswith("sugurta_") else "sugurta_uz"
    return None


def find_plate(uid, caption: str):
    """Izohdagi fura raqami. -> (raqam, mavjud_fura_yoki_None)"""
    if not caption:
        return None, None
    compact = dbm.norm_plate(caption)
    vs = dbm.list_vehicles(uid)
    # 1) bazadagi furalardan biri izohda bormi (eng uzunidan - 01928WNA va 1928WN adashmasin)
    for v in sorted(vs, key=lambda x: -len(x["plate"] or "")):
        p = v["plate"] or ""
        if len(p) >= 5 and p in compact:
            return p, v
    # 2) raqam shabloni
    up = caption.upper().translate(_CYR2LAT)
    for rx in _PLATE_RES:
        for m in rx.finditer(up):
            p = dbm.norm_plate(m.group(1))
            if 6 <= len(p) <= 10 and re.search(r"\d", p) and re.search(r"[A-Z]", p):
                v = next((x for x in vs if x["plate"] == p), None)
                return p, v
    return None, None


def find_driver(uid, caption: str):
    """Izohda bazadagi haydovchilardan biri tilga olinganmi. -> haydovchi yoki None"""
    t = _low(caption)
    if not t:
        return None
    best, score = None, 0
    for d in dbm.list_drivers(uid):
        words = [w for w in re.split(r"\s+", _low(d["name"])) if len(w) >= 3]
        if not words:
            continue
        hit = sum(1 for w in words if re.search(rf"(?<!\w){re.escape(w)}{_SUFFIXES}(?!\w)", t))
        if hit and (hit > score or (hit == score and len(words) < len(best["name"].split()))):
            best, score = d, hit
    return best


_EXTRA_WORDS = re.compile(r"^(orqa|oldi|orka|tarafi|tomoni|bet|beti|sahifa|орқа|орка|олди|тарафи|томони|обратн\w*|"
                          r"лицев\w*|сторон\w*|back|front|side|page|haydovchi\w*|ҳайдовчи\w*|водител\w*|driver|"
                          r"yangi|новый|новая|hujjat\w*|ҳужжат\w*|документ\w*|document|rasm|фото|photo|va|и|and|"
                          r"uchun|для|of|the|ning|нинг|karta\w*|карта\w*|card)$")


def leftover_name(caption: str):
    """Izohdan kalit so'zlar, raqamlar va sanalar olib tashlangach qolgan ism (1-4 so'z) yoki None.
    So'z butunligicha olib tashlanadi: «pravasi», «техпаспорт» kabi so'zlardan qoldiq qolmaydi."""
    words = []
    for w in re.split(r"[\s,;:/|()\-–—]+", caption or ""):
        w = w.strip(".!?\"'«»")
        lw = _low(w)
        if len(w) < 2 or re.search(r"\d", w) or _EXTRA_WORDS.match(lw):
            continue
        if any(re.search(pat, lw) for pat, _ in _DOC_RULES + _COUNTRY):
            continue
        if not re.fullmatch(r"[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ']+", w):
            continue
        if len(w) <= 3 and w.isupper():  # «BA», «WNA» - raqam qoldig'i
            continue
        w = re.sub(r"(ning|niki|нинг|ники)$", "", w) if len(w) > 7 else w  # «Aliyevning» -> «Aliyev»
        words.append(w)
    if 1 <= len(words) <= 4 and any(len(w) >= 3 for w in words):
        return " ".join(w[:1].upper() + w[1:] for w in words)
    return None


def remember_plate(uid, plate):
    if plate:
        LAST_PLATE[uid] = (plate, time.time())


def recent_plate(uid, within=600):
    v = LAST_PLATE.get(uid)
    return v[0] if v and time.time() - v[1] < within else None


def group_caption(media_group_id, caption):
    """Albom: izoh birinchi rasmda bo'ladi - qolganlariga ham shu izoh qo'llanadi."""
    now = time.time()
    for k in [k for k, (_, t) in GROUP_CAPTION.items() if now - t > 300]:
        GROUP_CAPTION.pop(k, None)
    if not media_group_id:
        return caption
    if caption:
        GROUP_CAPTION[media_group_id] = (caption, now)
        return caption
    v = GROUP_CAPTION.get(media_group_id)
    return v[0] if v else caption
