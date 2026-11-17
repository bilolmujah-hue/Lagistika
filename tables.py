"""Rangli jadvallar: Telegram uchun rasm (PNG) va Excel fayl.
Ranglar: qizil = 7 kun yoki undan kam / muddati o'tgan, sariq = 30 kungacha, yashil = 30 kundan ko'p."""
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

import config
import database as dbm

# holat: (fon rangi, matn rangi)
COLORS = {
    "expired": ("#B71C1C", "white"),
    "red": ("#EF5350", "white"),
    "yellow": ("#FFD54F", "black"),
    "green": ("#81C784", "black"),
    "noexp": ("#B0BEC5", "black"),
    "missing": ("#ECEFF1", "#78909C"),
}
CHUNK = 25  # bitta rasmda eng ko'pi bilan nechta qator


def _cell_text(row, status, days) -> str:
    if status == "missing":
        return "-"
    if status == "noexp":
        return "bor"
    d = dbm.fmt_date(row["expiry_date"])
    if status == "expired":
        return f"{d}\nO'TGAN ({-days} kun)"
    return f"{d}\n({days} kun)"


def _build(uid, owner_type):
    """-> (sarlavhalar, qatorlar). Har qator: (nom, [(matn, holat), ...]). Eng shoshilinchi tepada."""
    if owner_type == "vehicle":
        owners = dbm.list_vehicles(uid)
        cfg = config.VEHICLE_DOCS_SHORT
        first_header = "Fura"
    else:
        owners = dbm.list_drivers(uid)
        cfg = config.DRIVER_DOCS_SHORT
        first_header = "Haydovchi"
    by_owner = dbm.docs_by_owner(uid, owner_type)

    rows = []
    for o in owners:
        docs = by_owner.get(o["id"], {})
        cells, worst = [], 10 ** 6
        for key in cfg:
            r = docs.get(key)
            st, d = dbm.doc_status(r)
            cells.append((_cell_text(r, st, d), st))
            if d is not None:
                worst = min(worst, d)
        if owner_type == "vehicle":
            name = o["plate"] + (f"\n{o['model']}" if o.get("model") else "")
        else:
            name = o["name"]
        rows.append((name, cells, worst))
    rows.sort(key=lambda x: x[2])
    return [first_header] + list(cfg.values()), rows


def _render_png(title, headers, rows) -> bytes:
    n_cols, n_rows = len(headers), len(rows)
    fig_w = max(9.0, 1.85 * n_cols)
    fig_h = 1.9 + 0.78 * (n_rows + 1)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.subplots_adjust(left=0.01, right=0.99, top=0.90, bottom=0.10)
    ax.axis("off")

    text = [[r[0]] + [c[0] for c in r[1]] for r in rows]
    tbl = ax.table(cellText=text, colLabels=headers, cellLoc="center", bbox=[0, 0, 1, 1])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)

    for (i, j), cell in tbl.get_celld().items():
        cell.set_edgecolor("white")
        cell.set_linewidth(2)
        if i == 0:  # sarlavha
            cell.set_facecolor("#263238")
            cell.get_text().set_color("white")
            cell.get_text().set_fontweight("bold")
        elif j == 0:  # birinchi ustun (nomi)
            cell.set_facecolor("#F5F5F5")
            cell.get_text().set_fontweight("bold")
        else:
            bg, fg = COLORS[rows[i - 1][1][j - 1][1]]
            cell.set_facecolor(bg)
            cell.get_text().set_color(fg)

    ax.set_title(title, fontsize=16, fontweight="bold", pad=14)
    handles = [
        Patch(color=COLORS["green"][0], label=f"{config.YELLOW_DAYS} kundan ko'p"),
        Patch(color=COLORS["yellow"][0], label=f"{config.RED_DAYS + 1}-{config.YELLOW_DAYS} kun"),
        Patch(color=COLORS["red"][0], label=f"{config.RED_DAYS} kun va kam"),
        Patch(color=COLORS["expired"][0], label="muddati o'tgan"),
        Patch(color=COLORS["noexp"][0], label="bor (muddatsiz)"),
        Patch(color=COLORS["missing"][0], label="hujjat yo'q"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=6, frameon=False, fontsize=10)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def table_pngs(uid, owner_type) -> list:
    """Jadval rasmlari ro'yxati (qatorlar ko'p bo'lsa, bir nechta rasmga bo'linadi)."""
    headers, rows = _build(uid, owner_type)
    if not rows:
        return []
    label = "Furalar" if owner_type == "vehicle" else "Haydovchilar"
    today = dbm.fmt_date(dbm.today().isoformat())
    parts = [rows[i:i + CHUNK] for i in range(0, len(rows), CHUNK)]
    out = []
    for n, part in enumerate(parts, 1):
        suffix = f" ({n}/{len(parts)})" if len(parts) > 1 else ""
        out.append(_render_png(f"{label} hujjatlari holati - {today}{suffix}", headers, part))
    return out


# ====================================================================== EXCEL

def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color.lstrip("#"))


def excel_bytes(uid) -> bytes:
    wb = Workbook()
    thin = Side(style="thin", color="FFFFFF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for idx, (owner_type, title) in enumerate([("vehicle", "Furalar"), ("driver", "Haydovchilar")]):
        ws = wb.active if idx == 0 else wb.create_sheet()
        ws.title = title
        headers, rows = _build(uid, owner_type)
        ws.append([h.replace("\n", " ") for h in headers])
        for c in ws[1]:
            c.fill, c.font = _fill("#263238"), Font(bold=True, color="FFFFFF")
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for name, cells, _ in rows:
            ws.append([name.replace("\n", " - ")] + [t.replace("\n", " ") for t, _ in cells])
            r = ws.max_row
            ws.cell(r, 1).font = Font(bold=True)
            for j, (_, st) in enumerate(cells, start=2):
                bg, fg = COLORS[st]
                cell = ws.cell(r, j)
                cell.fill = _fill(bg)
                cell.font = Font(color=fg.lstrip("#") if fg.startswith("#") else
                                 ("FFFFFF" if fg == "white" else "000000"))
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = border
        ws.column_dimensions["A"].width = 28
        for j in range(2, len(headers) + 1):
            ws.column_dimensions[get_column_letter(j)].width = 20
        ws.row_dimensions[1].height = 34
        ws.freeze_panes = "B2"

    ws = wb.create_sheet("Eslatmalar")
    ws.append(["Turi", "Fura", "Sana", "Holati"])
    for c in ws[1]:
        c.fill, c.font = _fill("#263238"), Font(bold=True, color="FFFFFF")
    for r in dbm.list_reminders(uid, only_open=False):
        ws.append([dbm.reminder_title(r), r.get("plate") or "-", dbm.fmt_date(r["remind_date"]),
                   "bajarilgan" if r["done"] else "kutilmoqda"])
    for col, w in zip("ABCD", (48, 16, 14, 14)):
        ws.column_dimensions[col].width = w

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
