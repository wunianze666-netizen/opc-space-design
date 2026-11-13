"""A client-facing comparison from the audited layouts and matching images."""

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from layout import TEMPLATES, strategy_text
from model import PRIORITIES
from visual import ACCENT, BG, INK, MUTED, PALETTES, font, shorten


WIDTH = 1280
MARGIN = 58
CARD_HEIGHT = 452
GAP = 20
LINE = "#d8ddd3"
CARD = "#ffffff"
METRICS = {
    "visitor": ("访客区平均直线距", "visitor_straight_line_m", " m"),
    "focus": ("舱至活跃区净距", "focus_separation_m", " m"),
    "comfort": ("连通通行中心区", "connected_clear_area_m2", " m²"),
    "privacy": ("入口视线遮挡采样", "private_screened_percent", "%"),
}
CHECK_LABELS = {
    "packing": "使用空间与布局", "program": "功能数量", "containment": "边界容纳",
    "furniture_collision": "家具使用空间重叠", "fixed_and_doors": "固定结构和门扇净空",
    "assigned_zones": "指定分区", "exit_access": "出口可达", "continuous_access": "室内通行",
    "travel_distance": "路径长度", "ceiling": "层高", "budget": "预算",
    "protected_routes": "指定通道", "required_approaches": "指定门前可达", "locked_placements": "家具定位",
}
PENDING_LABELS = {
    "budget": "报价", "ceiling": "层高", "travel_distance": "路径长度",
    "interior_installation": "饰面与安装",
    "fire_and_structure": "消防、结构及机电专项",
}


def text_line(draw, xy, text, size, width, fill=INK, *, bold=False, anchor=None):
    face = font(size, bold)
    draw.text(xy, shorten(draw, text, face, width), fill=fill, font=face, anchor=anchor)


def wrap_lines(draw, text, size, width, limit, bold=False):
    face = font(size, bold)
    lines, line = [], ""
    text = " ".join(str(text).split())
    for char in text:
        if line and draw.textlength(line + char, font=face) > width:
            lines.append(line.rstrip())
            line = char.lstrip()
        else:
            line += char
    if line:
        lines.append(line.rstrip())
    if len(lines) > limit:
        lines = lines[:limit - 1] + [shorten(draw, lines[limit - 1] + "…", face, width)]
    return lines


def crop_board(image, kind):
    if kind == "render":
        x, y = round(image.width * .06), round(image.height * .06)
        return image.crop((x, y, image.width - x, image.height - y))
    if kind == "plan" and image.size == (1560, 1100):
        image = image.crop((0, 146, 1560, 984))
    elif kind == "axon" and image.size == (1350, 975):
        image = image.crop((0, 146, 1350, 916))
    else:
        return image
    difference = ImageChops.difference(image, Image.new("RGB", image.size, BG))
    bbox = difference.point(lambda value: 255 if value > 10 else 0).getbbox()
    if bbox:
        x0, y0, x1, y1 = bbox
        image = image.crop((max(0, x0 - 12), max(0, y0 - 12),
                            min(image.width, x1 + 12), min(image.height, y1 + 12)))
    return image


def picture(board, draw, path, kind, box):
    x, y, w, h = box
    draw.rounded_rectangle((x, y, x + w, y + h), radius=13, fill=BG)
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            background = Image.new("RGBA", image.size, BG)
            background.alpha_composite(image)
            image = crop_board(background.convert("RGB"), kind)
    except OSError:
        text_line(draw, (x + w / 2, y + h / 2), "图件待补", 32, w - 30, MUTED, anchor="mm")
        return
    image = ImageOps.contain(image, (w - 22, h - 18), Image.Resampling.LANCZOS)
    board.paste(image, (x + (w - image.width) // 2, y + (h - image.height) // 2))


def metric_value(key, metrics):
    if key == "visitor" and metrics.get("visitor_unconnected_groups"):
        return f"{metrics['visitor_unconnected_groups']} 区未连通"
    _, field, unit = METRICS[key]
    value = metrics.get(field)
    return None if value is None else f"{value:g}{unit}"


def comparison_keys(ranking, priorities):
    ordered = list(dict.fromkeys([*(key for key in priorities if key in METRICS), *METRICS]))
    values = {key: {value for row in ranking if (value := metric_value(key, row.get("metrics", {}))) is not None}
              for key in ordered}
    distinct = [key for key in ordered if len(values[key]) > 1]
    comparable = [key for key in ordered if values[key] and key not in distinct]
    return (distinct + comparable)[:2]


def money(cents):
    yuan, remainder = divmod(int(cents), 100)
    return f"¥{yuan:,}" + (f".{remainder:02d}" if remainder else "")


def quote(audit):
    missing = len(audit.get("missing_prices", []))
    items = audit.get("items", [])
    if not any(row.get("amount_cents") is not None for row in items):
        return f"{missing} 项待报价" if missing else "报价待确认"
    return f"已报 {money(audit.get('subtotal_cents', 0))}" + (f" · {missing} 项待报" if missing else "")


def program(project, options):
    requirements = project.req
    desks = requirements.get("desks", 0)
    meetings = requirements.get("meeting_seats", [])
    groups = requirements.get("work_groups", {})
    interior = requirements.get("interior")
    return " · ".join([
        f"{project.outline.area / 1e6:.1f} m²",
        *([f"{desks} 工位"] if desks else []),
        *([f"{len(groups)} 团队区"] if groups else []),
        *([f"{sum(meetings)} 会议座"] if meetings else []),
        *([f"{len(interior['elements'])} 项室内设计"] if interior else []),
        requirements.get("style", ""),
        f"{len(options)} 套候选",
    ]).replace(" ·  · ", " · ")


def option_card(board, draw, y, project, root, option, badge, keys, metrics, accent, extra_height=0):
    x, w = MARGIN, WIDTH - 2 * MARGIN
    draw.rounded_rectangle((x, y, x + w, y + CARD_HEIGHT + extra_height), radius=20,
                           fill=CARD, outline=accent if badge else LINE, width=3 if badge else 1)
    name, _ = strategy_text(project, option["strategy"])
    groups = [TEMPLATES[p["type"]].desks for p in option["placements"] if p["type"] in TEMPLATES
              and TEMPLATES[p["type"]].desks]
    if not project.req["work_groups"]:
        if len(groups) == 1 and groups[0] > 1:
            name = f"{groups[0]} 席集中"
        elif len(groups) > 1 and all(size == 1 for size in groups):
            name = f"{len(groups)} 个独立工位"
        elif len(groups) > 1 and len(set(groups)) == 1:
            name = f"{len(groups)} 组 · 每组 {groups[0]} 席"
    price = quote(option["audit"])
    right = x + w - 32
    price_width = min(470, int(draw.textlength(price, font=font(31))) + 6)
    title_x = x + 32
    if badge:
        badge_width = int(draw.textlength(badge, font=font(27, True))) + 28
        draw.rounded_rectangle((title_x, y + 26, title_x + badge_width, y + 68), radius=8, fill=accent)
        draw.text((title_x + 14, y + 25), badge, font=font(27, True), fill="white")
        title_x += badge_width + 16
    text_line(draw, (title_x, y + 18), f"{option['id']}  {name}", 42, right - price_width - title_x - 25, bold=True)
    text_line(draw, (right, y + 28), price, 31, price_width, MUTED, anchor="ra")

    folder = root / option["id"]
    eye = folder / "人视效果.png"
    render = folder / "精渲染.png"
    effect, kind = ((eye, "eye") if eye.is_file() else (render, "render") if render.is_file()
                    else (folder / "轴测-1.png", "axon"))
    col = (w - 64 - 20) // 2
    left = x + 32
    mid = left + col + 20
    text_line(draw, (left, y + 83), "平面图", 29, col, MUTED)
    text_line(draw, (mid, y + 83), {"eye": "室内人视", "render": "效果示意", "axon": "轴测示意"}[kind], 29, col, MUTED)
    picture(board, draw, folder / "平面图.png", "plan", (left, y + 123, col, 241 + extra_height))
    picture(board, draw, effect, kind, (mid, y + 123, col, 241 + extra_height))

    for key, xx in zip(keys, (left, mid)):
        value = metric_value(key, metrics) or "待核"
        label = METRICS[key][0]
        value_width = min(230, int(draw.textlength(value, font=font(36, True))) + 6)
        text_line(draw, (xx, y + 387 + extra_height), label, 32, col - value_width - 14, MUTED)
        text_line(draw, (xx + col, y + 379 + extra_height), value, 36, value_width, accent, bold=True, anchor="ra")


def conflicts(draw, y, stats, accent):
    x, w = MARGIN, WIDTH - 2 * MARGIN
    draw.rounded_rectangle((x, y, x + w, y + 420), radius=20, fill=CARD, outline=LINE, width=1)
    text_line(draw, (x + 32, y + 26), "当前搜索未找到通过约束的方案", 44, w - 64, bold=True)
    reasons = stats.get("rejected", {})
    known = [f"{CHECK_LABELS[k]} {n} 次" for k, n in reasons.items() if n and k in CHECK_LABELS][:3]
    explanation = str(stats.get("reason", "")).split(" 搜索记录")[0]
    if not explanation:
        explanation = "检索结果仍需结合输入条件核查。"
    for i, line in enumerate(wrap_lines(draw, explanation, 34, w - 64, 3)):
        draw.text((x + 32, y + 96 + i * 47), line, font=font(34), fill=MUTED)
    if known:
        text_line(draw, (x + 32, y + 252), " · ".join(known), 31, w - 64, accent)
    budget = stats.get("examples_of_rejections", {}).get("budget", {})
    missing = budget.get("missing_prices", [])
    if missing:
        next_step = "补齐价格后重算。优先核对" + "、".join(missing[:3])
    elif reasons.get("budget"):
        next_step = "核对已报价范围和预算上限，再调整可替换家具。"
    elif reasons.get("protected_routes"):
        next_step = "核对指定通道与冲突构件的位置，再确认哪项条件可以调整。"
    elif reasons.get("required_approaches"):
        next_step = "核对指定入口、门前位置和中间的实体隔断或禁行区域。"
    else:
        next_step = "先核对图纸、出入口与保留设施，再调整可变的工位或配套。"
    for i, line in enumerate(wrap_lines(draw, next_step, 34, w - 64, 2)):
        draw.text((x + 32, y + 313 + i * 47), line, font=font(34), fill=INK)


def write_board(root, project, options, stats, revision, recommendation):
    """Write a PNG and one-page PDF for the validated options."""
    root = Path(root)
    recommendation = recommendation or {}
    ranking = recommendation.get("ranking", [])
    priorities = recommendation.get("applied_priorities", [])
    by_id = {row["id"]: row.get("metrics", {}) for row in ranking}
    keys = comparison_keys(ranking, priorities)
    ids = {option["id"] for option in options}
    first = recommendation.get("recommended_option")
    tied = [id_ for id_ in recommendation.get("tied_first_options", []) if id_ in ids]
    if len(tied) > 1:
        headline = "并列首选  " + "、".join(tied)
    elif first in ids and len(options) > 1:
        headline = f"建议选 {first}"
    elif len(options) == 1:
        headline = f"可行方案 {options[0]['id']}"
    else:
        headline = "查看可行候选"
    requested = stats.get("requested_options", project.req.get("options", len(options)))
    partial = stats.get("status") == "partial" or (stats.get("status") is None and 0 < len(options) < requested)
    accent = PALETTES.get(project.req.get("style"), {}).get("accent", ACCENT)

    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    design = project.req["interior"]
    title = wrap_lines(scratch, design["concept"] if design else project.title,
                       59, WIDTH - 2 * MARGIN, 2, bold=True) or ["空间方案"]
    narrative = wrap_lines(scratch, design["description"], 29, WIDTH - 2 * MARGIN, 3) if design else []
    header = 115 + 74 * len(title)
    after_narrative = header + (38 * len(narrative) + 8 if narrative else 0)
    y = after_narrative + (174 if options else 73)
    extra_height = 140 if design and len(options) == 1 else 0
    body_height = (CARD_HEIGHT + extra_height + GAP) * len(options) - GAP if options else 420
    height = y + body_height + (183 if stats.get("alignment_verified") else 157)
    board = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(board)
    text_line(draw, (MARGIN, 46), "空间方案比选", 34, 660, accent, bold=True)
    for i, line in enumerate(title):
        draw.text((MARGIN, 94 + i * 74), line, font=font(59, True), fill=INK)
    text_line(draw, (MARGIN, header), program(project, options), 31, WIDTH - 2 * MARGIN, MUTED)
    for i, line in enumerate(narrative):
        text_line(draw, (MARGIN, header + 47 + i * 38), line, 29, WIDTH - 2 * MARGIN, MUTED)

    if options:
        band = after_narrative + 54
        draw.rounded_rectangle((MARGIN, band, WIDTH - MARGIN, band + 99), radius=14, fill="#e9eee6")
        text_line(draw, (MARGIN + 24, band + 8), headline, 39, 720, accent, bold=True)
        priority_text = "优先比较 " + "、".join(PRIORITIES.get(p, p) for p in priorities) if priorities else "按已确认约束查看方案"
        if len(ranking) > 1 and first in ids and len(tied) <= 1:
            best, alternative = ranking[0].get("reasons", []), ranking[1].get("reasons", [])
            difference = next((a for a, b in zip(best, alternative) if a != b), None)
            if difference:
                priority_text = difference
        if partial:
            priority_text = f"当前找到 {len(options)} / {requested} 套 · " + priority_text
        text_line(draw, (MARGIN + 24, band + 57), priority_text, 28, WIDTH - 2 * MARGIN - 48, MUTED)
        for i, option in enumerate(options):
            id_ = option["id"]
            badge = "并列" if len(tied) > 1 and id_ in tied else "首选" if len(options) > 1 and id_ == first else ""
            option_card(board, draw, y + i * (CARD_HEIGHT + extra_height + GAP), project, root, option,
                        badge, keys, by_id.get(id_, {}), accent, extra_height)
    else:
        conflicts(draw, y, stats, accent)

    footer = y + body_height + 32
    draw.line((MARGIN, footer, WIDTH - MARGIN, footer), fill=LINE, width=2)
    pending = {check["id"] for option in options for check in option["audit"].get("checks", [])
               if check.get("status") == "pending"}
    if not any(option["audit"].get("missing_prices") for option in options):
        pending.discard("budget")
    names = [name for key, name in PENDING_LABELS.items() if key in pending]
    note = "待核 " + "、".join(names) if names else "尺寸及计量范围见方案包"
    if "cost" in recommendation.get("skipped_priorities", {}):
        note += " · 缺价项目补齐后比较预算"
    if stats.get("alignment_verified"):
        text_line(draw, (MARGIN, footer + 18), "平面、DXF 与三维几何已核对 · 实施前复尺",
                  29, WIDTH - 2 * MARGIN, accent)
        text_line(draw, (MARGIN, footer + 60), note, 26, WIDTH - 2 * MARGIN, MUTED)
    else:
        text_line(draw, (MARGIN, footer + 18), note, 30, WIDTH - 2 * MARGIN, MUTED)
    text_line(draw, (MARGIN, footer + (96 if stats.get("alignment_verified") else 64)),
              f"方案版本 {revision}" if stats.get("alignment_verified") else f"方案阶段 · 实施前复尺   /   {revision}",
              24, WIDTH - 2 * MARGIN, MUTED)

    board.save(root / "方案一页比选.png")
    page_width, page_height = A4
    scale = min((page_width - 20) / board.width, (page_height - 20) / board.height)
    w, h = board.width * scale, board.height * scale
    pdf = canvas.Canvas(str(root / "方案一页比选.pdf"), pagesize=A4)
    pdf.setTitle("空间方案比选")
    pdf.drawImage(ImageReader(board), (page_width - w) / 2, (page_height - h) / 2, w, h)
    pdf.showPage()
    pdf.save()
