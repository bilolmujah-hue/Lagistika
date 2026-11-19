"""Rangli jadvallar (ruscha): Telegram uchun rasm (PNG) va Excel fayl.

Ranglar: to'q qizil - muddati o'tgan, qizil - 7 kun va kam, sariq - 30 kungacha, yashil - 30 kundan ko'p,
kulrang-ko'k - muddatsiz hujjat, och kulrang - hujjat yo'q.
Fura/haydovchi kam bo'lsa (≤ 8) jadval "vertikal" chiziladi: qatorlar - hujjatlar, ustunlar - furalar.
Bu telefonda ancha yirik va o'qishga qulay ko'rinadi.
"""
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config
import database as dbm

# holat: (fon, matn)
COLORS = {
    "expired": ("#B71C1C", "#FFFFFF"),
    "red": ("#E53935", "#FFFFFF"),
    "yellow": ("#FFC107", "#1F2937"),
    "green": ("#43A047", "#FFFFFF"),
    "noexp": ("#78909C", "#FFFFFF"),
    "missing": ("#EEF1F4", "#9AA5B1"),
}
STATUS_RU = {"expired": "Просрочен", "red": "Срочно (≤7 дн.)", "yellow": "Скоро (≤30 дн.)",
             "green": "В норме", "noexp": "Бессрочный", "missing": "Нет документа"}
ORDER = {"expired": 0, "red": 1, "yellow": 2, "green": 3, "noexp": 4, "missing": 5}
HEADER_BG, NAME_BG, TITLE = "#1E293B", "#F1F5F9", "#0F172A"
TRANSPOSE_MAX = 8
CHUNK = 18


def _days_ru(n) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} дня"
    return f"{n} дней"


def _cell_text(row, status, days) -> str:
    if status == "missing":
        return "—"
    if status == "noexp":
        return "есть"
    d = dbm.fmt_date(row["expiry_date"])
    if status == "expired":
        return f"{d}\nпросрочен {_days_ru(days)}"
    if days == 0:
        return f"{d}\nистекает СЕГОДНЯ"
    return f"{d}\nосталось {_days_ru(days)}"


def _labels(owner_type):
    return config.VEHICLE_DOCS_RU if owner_type == "vehicle" else config.DRIVER_DOCS_RU


def _build(uid, owner_type):
    """-> [(nom, [(matn, holat, kun)...], eng_yomon_kun, egasi)], eng shoshilinchisi tepada."""
    if owner_type == "vehicle":
        owners = dbm.list_vehicles(uid)
    else:
        owners = dbm.list_drivers(uid)
    by_owner = dbm.docs_by_owner(uid, owner_type)
    rows = []
    for o in owners:
        docs = by_owner.get(o["id"], {})
        cells, worst = [], 10 ** 6
        for key in _labels(owner_type):
            r = docs.get(key)
            st, d = dbm.doc_status(r)
            cells.append((_cell_text(r, st, d), st, d))
            if d is not None:
                worst = min(worst, d)
        if owner_type == "vehicle":
            name = o["plate"] + (f"\n{o['model']}" if o.get("model") else "")
        else:
            name = o["name"]
        rows.append((name, cells, worst, o))
    rows.sort(key=lambda x: x[2])
    return rows


def _summary(rows) -> dict:
    cnt = {k: 0 for k in COLORS}
    for _, cells, _, _ in rows:
        for _, st, _ in cells:
            cnt[st] += 1
    return cnt


def _wrap(s, width=16):
    out = []
    for part in str(s).split("\n"):
        line = ""
        for w in part.split():
            if len(line) + len(w) + 1 > width and line:
                out.append(line)
                line = w
            else:
                line = (line + " " + w).strip()
        out.append(line)
    return "\n".join(out[:3])


def _box(ax, x, y, w, h, color, rad=0.07):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={rad}",
                                linewidth=0, facecolor=color))


def _render(title, subtitle, col_headers, row_headers, grid, corner):
    """grid[r][c] = (matn, holat). Hamma o'lchamlar dyuymda."""
    nr, nc = len(row_headers), len(col_headers)
    first_w, cw, hh, rh, gap = 2.5, 1.95, 0.95, 0.82, 0.06
    left = 0.3
    W = max(left * 2 + first_w + nc * cw, 7.5)
    # subtitr va legendani eniga qarab qatorlarga bo'lamiz
    maxc = int((W - 2 * left) / 0.092)
    sub_lines, cur = [], ""
    for part in subtitle.split("   ·   "):
        cand = (cur + "   ·   " + part) if cur else part
        if len(cand) > maxc and cur:
            sub_lines.append(cur)
            cur = part
        else:
            cur = cand
    sub_lines.append(cur)
    leg_rows, row, lx = [], [], 0.0
    for st in ("expired", "red", "yellow", "green", "noexp", "missing"):
        wdt = 0.5 + len(STATUS_RU[st]) * 0.085
        if lx + wdt > W - 2 * left and row:
            leg_rows.append(row)
            row, lx = [], 0.0
        row.append((st, lx))
        lx += wdt
    leg_rows.append(row)
    top = 1.15 + 0.3 * len(sub_lines)
    bottom = 0.45 + 0.4 * len(leg_rows)
    H = top + hh + nr * rh + bottom
    fig = plt.figure(figsize=(W, H), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    ax.text(left, 0.5, title, fontsize=20, fontweight="bold", color=TITLE, va="center")
    for i, ln in enumerate(sub_lines):
        ax.text(left, 0.98 + 0.3 * i, ln, fontsize=11.5, color="#475569", va="center")

    y = top
    _box(ax, left, y, first_w - gap, hh - gap, HEADER_BG)
    ax.text(left + 0.15, y + (hh - gap) / 2, corner, fontsize=12, color="white", fontweight="bold", va="center")
    for c, h in enumerate(col_headers):
        x = left + first_w + c * cw
        _box(ax, x, y, cw - gap, hh - gap, HEADER_BG)
        ax.text(x + (cw - gap) / 2, y + (hh - gap) / 2, _wrap(h, 15), fontsize=10.5, color="white",
                fontweight="bold", ha="center", va="center", linespacing=1.15)
    for r, rname in enumerate(row_headers):
        y = top + hh + r * rh
        _box(ax, left, y, first_w - gap, rh - gap, NAME_BG)
        ax.text(left + 0.15, y + (rh - gap) / 2, _wrap(rname, 22), fontsize=11, color=TITLE,
                fontweight="bold", va="center", linespacing=1.15)
        for c in range(nc):
            text, st = grid[r][c]
            bg, fg = COLORS[st]
            x = left + first_w + c * cw
            _box(ax, x, y, cw - gap, rh - gap, bg)
            bold = st in ("expired", "red")
            ax.text(x + (cw - gap) / 2, y + (rh - gap) / 2, text, fontsize=10.5 if st != "missing" else 14,
                    color=fg, ha="center", va="center", fontweight="bold" if bold else "normal", linespacing=1.2)

    # legenda
    for i, row in enumerate(leg_rows):
        y = H - bottom + 0.35 + 0.4 * i
        for st, lx in row:
            x = left + lx
            _box(ax, x, y - 0.13, 0.26, 0.26, COLORS[st][0], rad=0.05)
            ax.text(x + 0.36, y, STATUS_RU[st], fontsize=10, color="#334155", va="center")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def table_pngs(uid, owner_type) -> list:
    rows = _build(uid, owner_type)
    if not rows:
        return []
    labels = list(_labels(owner_type).values())
    what = "Документы фур" if owner_type == "vehicle" else "Документы водителей"
    who = "Фура" if owner_type == "vehicle" else "Водитель"
    today = dbm.fmt_date(dbm.today().isoformat())
    cnt = _summary(rows)
    sub = (f"{'Фур' if owner_type == 'vehicle' else 'Водителей'}: {len(rows)}   ·   Просрочено: {cnt['expired']}   ·   "
           f"Срочно: {cnt['red']}   ·   Скоро: {cnt['yellow']}   ·   В норме: {cnt['green']}   ·   "
           f"Нет документа: {cnt['missing']}")
    out = []
    if len(rows) <= TRANSPOSE_MAX:
        # вертикально: строки - документы, столбцы - фуры
        col_headers = [r[0] for r in rows]
        grid = [[(rows[c][1][d][0], rows[c][1][d][1]) for c in range(len(rows))] for d in range(len(labels))]
        out.append(_render(f"{what} — {today}", sub, col_headers, [l.replace("\n", " ") for l in labels], grid,
                           "Документ"))
        return out
    parts = [rows[i:i + CHUNK] for i in range(0, len(rows), CHUNK)]
    for n, part in enumerate(parts, 1):
        suffix = f" ({n}/{len(parts)})" if len(parts) > 1 else ""
        grid = [[(c[0], c[1]) for c in r[1]] for r in part]
        out.append(_render(f"{what} — {today}{suffix}", sub, labels, [r[0] for r in part], grid, who))
    return out


# ====================================================================== EXCEL

THIN = Side(style="thin", color="D0D7DE")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)


def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color.lstrip("#"))


def _header(ws, row, values, height=42):
    for j, v in enumerate(values, start=1):
        c = ws.cell(row, j, v)
        c.fill, c.font, c.alignment, c.border = _fill(HEADER_BG), Font(bold=True, color="FFFFFF", size=11), CENTER, BORDER
    ws.row_dimensions[row].height = height


def _status_cell(c, st):
    bg, fg = COLORS[st]
    c.fill = _fill(bg)
    c.font = Font(color=fg.lstrip("#"), bold=st in ("expired", "red"), size=10)
    c.alignment, c.border = CENTER, BORDER


def _nearest(cells, labels):
    best = None
    for (text, st, d), lab in zip(cells, labels):
        if d is not None and (best is None or d < best[0]):
            best = (d, lab.replace("\n", " "), text.replace("\n", ", "), st)
    return best


def _owner_sheet(ws, uid, owner_type):
    rows = _build(uid, owner_type)
    labels = list(_labels(owner_type).values())
    if owner_type == "vehicle":
        fixed = ["№", "Фура", "Модель", "Водитель"]
    else:
        fixed = ["№", "Водитель", "Телефон", "Фуры", "В боте"]
    title = ("Документы фур" if owner_type == "vehicle" else "Документы водителей") + \
            f" — {dbm.fmt_date(dbm.today().isoformat())}"
    ws.cell(1, 1, title).font = Font(bold=True, size=15, color=TITLE[1:])
    ws.row_dimensions[1].height = 26
    head = fixed + [l.replace("\n", " ") for l in labels] + ["Ближайший срок", "Общий статус"]
    _header(ws, 3, head)
    r = 4
    for i, (name, cells, worst, o) in enumerate(rows, 1):
        if owner_type == "vehicle":
            drv = dbm.get_driver(uid, o["driver_id"]) if o.get("driver_id") else None
            vals = [i, o["plate"], o.get("model") or "—", drv["name"] if drv else "—"]
        else:
            cars = dbm.vehicles_of_driver(uid, o["id"])
            vals = [i, o["name"], o.get("phone") or "—", ", ".join(c["plate"] for c in cars) or "—",
                    "да" if o.get("tg_user_id") else "нет"]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(r, j, v)
            c.alignment, c.border = (CENTER if j == 1 else LEFT), BORDER
            c.font = Font(bold=(j == 2), size=10)
        for k, (text, st, d) in enumerate(cells):
            c = ws.cell(r, len(fixed) + 1 + k, text.replace("\n", "\n"))
            _status_cell(c, st)
        nb = _nearest(cells, labels)
        c1 = ws.cell(r, len(head) - 1, f"{nb[1]}: {nb[2]}" if nb else "—")
        c1.alignment, c1.border, c1.font = LEFT, BORDER, Font(size=10)
        worst_st = min((st for _, st, _ in cells if st not in ("missing", "noexp")), key=lambda s: ORDER[s],
                       default="missing")
        c2 = ws.cell(r, len(head), STATUS_RU[worst_st] if worst_st != "missing" else "Нет сроков")
        _status_cell(c2, worst_st if worst_st != "missing" else "noexp")
        ws.row_dimensions[r].height = 34
        r += 1
    widths = ([5, 16, 22, 22] if owner_type == "vehicle" else [5, 24, 16, 18, 8]) + [16] * len(labels) + [34, 16]
    for j, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = ws.cell(4, 3)
    if rows:
        ws.auto_filter.ref = f"A3:{get_column_letter(len(head))}{r - 1}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return rows


def _reminder_title(r) -> str:
    base = config.REMINDER_LABELS_RU.get(r["kind"], "Напоминание")
    if r.get("title"):
        return r["title"] if r["kind"] == "custom" else f"{base}: {r['title']}"
    return base


def excel_bytes(uid) -> bytes:
    wb = Workbook()
    summary = wb.active
    summary.title = "Сводка"
    v_ws = wb.create_sheet("Фуры")
    d_ws = wb.create_sheet("Водители")
    v_rows = _owner_sheet(v_ws, uid, "vehicle")
    d_rows = _owner_sheet(d_ws, uid, "driver")

    # --- Напоминания
    ws = wb.create_sheet("Напоминания")
    ws.cell(1, 1, "Напоминания и очереди").font = Font(bold=True, size=15, color=TITLE[1:])
    _header(ws, 3, ["Тип / задача", "Фура", "Дата", "Осталось", "Статус"], 30)
    r = 4
    today = dbm.today()
    for rem in dbm.list_reminders(uid, only_open=False):
        d = (dbm._dt(rem["remind_date"] + "T00:00:00").date() - today).days if rem.get("remind_date") else None
        if rem["done"]:
            st_txt, st = "выполнено", "green"
        elif d is not None and d < 0:
            st_txt, st = "просрочено", "expired"
        elif d is not None and d <= 3:
            st_txt, st = "скоро", "yellow"
        else:
            st_txt, st = "ожидается", "noexp"
        vals = [_reminder_title(rem), rem.get("plate") or "—", dbm.fmt_date(rem["remind_date"]),
                (_days_ru(d) if d is not None and d >= 0 else ("—" if d is None else f"-{_days_ru(d)}"))]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(r, j, v)
            c.alignment, c.border, c.font = (LEFT if j == 1 else CENTER), BORDER, Font(size=10)
        _status_cell(ws.cell(r, 5, st_txt), st)
        ws.row_dimensions[r].height = 22
        r += 1
    for col, w in zip("ABCDE", (48, 14, 13, 13, 14)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A4"

    # --- Сводка
    s = summary
    s.cell(1, 1, "Отчёт по документам").font = Font(bold=True, size=18, color=TITLE[1:])
    s.cell(2, 1, f"Сформирован: {dbm.fmt_dt(dbm.now_iso(), True)}").font = Font(size=10, color="64748B")
    _header(s, 4, ["", "Фуры", "Водители"], 26)
    cv, cd = _summary(v_rows), _summary(d_rows)
    lines = [("Всего", len(v_rows), len(d_rows), None)] + \
            [(STATUS_RU[k], cv[k], cd[k], k) for k in ("expired", "red", "yellow", "green", "noexp", "missing")]
    for i, (lab, a, b, st) in enumerate(lines, start=5):
        c = s.cell(i, 1, lab)
        if st:
            _status_cell(c, st)
            c.alignment = LEFT
        else:
            c.font, c.border = Font(bold=True), BORDER
        for j, v in ((2, a), (3, b)):
            cc = s.cell(i, j, v)
            cc.alignment, cc.border, cc.font = CENTER, BORDER, Font(bold=True, size=11)
        s.row_dimensions[i].height = 22
    s.merge_cells("A13:C16")
    s.cell(13, 1).alignment = Alignment(wrap_text=True, vertical="top")
    s.cell(13, 1, "Цвета: тёмно-красный — просрочен, красный — 7 дней и меньше, жёлтый — до 30 дней, "
                  "зелёный — больше 30 дней, серый — бессрочный, светлый — документа нет.").font = Font(size=9, color="64748B")
    s.column_dimensions["A"].width = 28
    s.column_dimensions["B"].width = 14
    s.column_dimensions["C"].width = 14

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
