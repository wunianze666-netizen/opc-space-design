"""Locate scheduled items on a copy of the verified plan and DXF."""

from collections import defaultdict
from math import cos, fsum, hypot, pi, sin
from pathlib import Path
from tempfile import TemporaryDirectory

import ezdxf
from ezdxf.lldxf.tagwriter import TagCollector
from PIL import Image, ImageDraw

from model import InputError
from visual import BG, INK, MUTED, font, plan_image, shorten


PNG_NAME = "采购定位图.png"
DXF_NAME = "采购定位.dxf"
REFERENCE_LAYER = "OPC_PROCUREMENT_REF"
REFERENCE_APP = "OPC_PROCUREMENT"
CATEGORIES = {
    "furniture": ("家具与设备", "#355c6e"),
    "wall": ("墙面饰件", "#8b553b"),
    "ceiling": ("吊顶构件与灯具", "#655689"),
    "floor": ("地面饰面", "#507257"),
}
SIDE_WIDTH = 425


def plan_transform(project):
    """Match the plan_image drawing frame in project millimeters."""
    x0, y0, x1, y1 = project.outline.bounds
    scale = min(1220 / (x1 - x0), 655 / (y1 - y0))
    left = 780 - (x1 - x0) * scale / 2
    bottom = 200 + (655 + (y1 - y0) * scale) / 2

    def xy(point):
        x, y = point
        return left + (x - x0) * scale, bottom - (y - y0) * scale

    return xy


def representative(locators):
    center_x = fsum(ref["center_mm"][0] for ref in locators) / len(locators)
    center_y = fsum(ref["center_mm"][1] for ref in locators) / len(locators)
    return min(locators, key=lambda ref: (hypot(ref["center_mm"][0] - center_x, ref["center_mm"][1] - center_y),
                                          ref["id"]))


def map_groups(schedule):
    by_item = defaultdict(list)
    for ref in schedule["locators"]:
        by_item[ref["item_id"]].append(ref)
    for category in CATEGORIES:
        for item in schedule["items"]:
            refs = by_item[item["id"]]
            if item["category"] != category or not refs:
                continue
            clusters = defaultdict(list)
            for ref in refs:
                clusters[ref.get("group_label") or ""].append(ref)
            anchors = [representative(cluster) for cluster in clusters.values()]
            yield item, anchors


def map_pages(groups):
    """Keep a crowded map legible by continuing on another copy of the same plan."""
    page = []
    markers = 0
    for group in groups:
        count = len(group[1])
        if page and markers + count > 30:
            yield page
            page, markers = [], 0
        page.append(group)
        markers += count
    if page:
        yield page


def free_box(center, width, height, occupied):
    x, y = center
    box = (x - width / 2, y - height / 2, x + width / 2, y + height / 2)
    if box[0] < 64 or box[1] < 174 or box[2] > 1496 or box[3] > 891:
        return None
    for a, b, c, d in occupied:
        if box[0] < c + 7 and a - 7 < box[2] and box[1] < d + 7 and b - 7 < box[3]:
            return None
    return box


def badge_box(anchor, width, occupied):
    ax, ay = anchor
    for radius in (29, 42, 58, 79, 110, 147, 195):
        for sector in (2, 6, 1, 3, 5, 7, 0, 4):
            angle = sector * pi / 4
            box = free_box((ax + radius * cos(angle), ay + radius * sin(angle)), width, 29, occupied)
            if box:
                return box
    # This covers dense or narrow plan shapes while keeping leaders attached to their true anchors.
    candidates = ((x, y) for y in range(190, 879, 36) for x in range(102, 1460, 82))
    for center in sorted(candidates, key=lambda p: hypot(p[0] - ax, p[1] - ay)):
        box = free_box(center, width, 29, occupied)
        if box:
            return box
    raise InputError("采购定位图无法排开编号，请检查清单是否存在异常长的编号。")


def draw_markers(draw, groups, xy):
    typeface = font(17, True)
    seeds = [(item["id"], CATEGORIES[item["category"]][1], xy(ref["center_mm"]))
             for item, anchors in groups for ref in anchors]
    occupied = [(x - 4, y - 4, x + 4, y + 4) for _, _, (x, y) in seeds]
    placed = []
    for label, color, point in seeds:
        width = draw.textlength(label, font=typeface) + 22
        box = badge_box(point, width, occupied)
        occupied.append(box)
        placed.append((label, color, point, box))
    for _, color, (x, y), (left, top, right, bottom) in placed:
        end = (max(left, min(x, right)), max(top, min(y, bottom)))
        draw.line(((x, y), end), fill="#fffdf7", width=5)
        draw.line(((x, y), end), fill=color, width=2)
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color, outline="#fffdf7", width=1)
    for label, color, _, (left, top, right, bottom) in placed:
        draw.rounded_rectangle((left, top, right, bottom), radius=5, fill=color, outline="#fffdf7", width=1)
        draw.text(((left + right) / 2, (top + bottom) / 2), label, font=typeface, fill="white", anchor="mm")


def legend_height(groups):
    return 210 + 32 * len(groups) + 40 * len({item["category"] for item, _ in groups})


def draw_legend(draw, left, height, layout, groups, page_number, page_count):
    draw.rounded_rectangle((left, 38, left + SIDE_WIDTH - 26, height - 36), radius=16,
                           fill="#fbfaf5", outline="#ccd4c8", width=2)
    x = left + 24
    draw.text((x, 66), "采购定位", font=font(33, True), fill=INK)
    title = f"方案 {layout['id']} · 清单编号"
    if page_count > 1:
        title += f" · 第 {page_number} / {page_count} 页"
    draw.text((x, 113), title, font=font(18), fill=MUTED)
    draw.text((x, 145), "图中标出各组代表位置。逐件位置见 DXF。", font=font(16), fill=MUTED)
    draw.line((x, 182, left + SIDE_WIDTH - 52, 182), fill="#ccd4c8", width=1)
    y = 199
    current = None
    for item, _ in groups:
        category = item["category"]
        section, color = CATEGORIES[category]
        if category != current:
            draw.ellipse((x + 1, y + 8, x + 9, y + 16), fill=color)
            draw.text((x + 18, y), section, font=font(19, True), fill=INK)
            y += 40
            current = category
        draw.text((x, y), item["id"], font=font(16, True), fill=color)
        qty = f"{item['quantity']:g} {item['unit']}"
        right = left + SIDE_WIDTH - 52
        text_font = font(16)
        available = right - x - 72 - draw.textlength(qty, font=text_font) - 10
        title = shorten(draw, item["name"], text_font, available)
        draw.text((x + 71, y), title, font=text_font, fill=INK)
        draw.text((right, y), qty, font=text_font, fill=MUTED, anchor="ra")
        y += 32


def locator_image(project, layout, schedule):
    groups = list(map_groups(schedule))
    pages = list(map_pages(groups)) or [[]]
    base = plan_image(project, layout["placements"], f"方案 {layout['id']}", layout["strategy"])
    if base.size != (1560, 1100):
        raise InputError("当前平面图尺寸与采购定位绘图框不一致。")
    xy = plan_transform(project)
    heights = [max(base.height, legend_height(page) + 44) for page in pages]
    image = Image.new("RGB", (base.width + SIDE_WIDTH + 22, sum(heights)), BG)
    top = 0
    for number, (page, height) in enumerate(zip(pages, heights), 1):
        diagram = base.copy()
        draw_markers(ImageDraw.Draw(diagram), page, xy)
        board = Image.new("RGB", (image.width, height), BG)
        board.paste(diagram, (0, 0))
        draw_legend(ImageDraw.Draw(board), base.width + 10, height, layout, page, number, len(pages))
        image.paste(board, (0, top))
        top += height
    return image


def entity_snapshot(doc):
    """Compare the source model and every named block independently of the label layer."""
    return {block.name: [tuple(TagCollector.dxftags(entity, doc.dxfversion)) for entity in block
                         if entity.dxf.layer != REFERENCE_LAYER] for block in doc.blocks}


def export_annotated_dxf(source, destination, project, schedule):
    doc = ezdxf.readfile(source)
    if REFERENCE_LAYER in doc.layers or REFERENCE_APP in doc.appids:
        raise InputError("源 DXF 已包含采购定位注释，请使用已核验的原始方案图。")
    original = entity_snapshot(doc)
    doc.layers.new(REFERENCE_LAYER, dxfattribs={"color": 6})
    doc.appids.new(REFERENCE_APP)
    model = doc.modelspace()
    x0, y0, x1, y1 = project.outline.bounds
    height = max(68, min(175, max(x1 - x0, y1 - y0) / 180))
    for ref in schedule["locators"]:
        x, y = ref["center_mm"]
        metadata = [(1000, ref["id"]), (1000, ref["item_id"]), (1000, ref["category"]),
                    (1040, ref["base_mm"]), (1040, ref["top_mm"])]
        point = model.add_point((x, y, 0), dxfattribs={"layer": REFERENCE_LAYER})
        point.set_xdata(REFERENCE_APP, metadata)
        text = model.add_text(f"{ref['id']} / {ref['item_id']}",
                              dxfattribs={"layer": REFERENCE_LAYER, "height": height,
                                          "insert": (x + .42 * height, y + .34 * height, 0)})
        text.set_xdata(REFERENCE_APP, metadata)
    doc.saveas(destination)
    saved = ezdxf.readfile(destination)
    if entity_snapshot(saved) != original:
        raise InputError("采购标注 DXF 的原有实体与源图不一致。")
    layer = saved.modelspace().query(f'*[layer=="{REFERENCE_LAYER}"]')
    points = {entity.get_xdata(REFERENCE_APP)[0].value: tuple(entity.dxf.location)[:2]
              for entity in layer if entity.dxftype() == "POINT"}
    labels = {entity.get_xdata(REFERENCE_APP)[0].value: entity.dxf.text
              for entity in layer if entity.dxftype() == "TEXT"}
    expected = {ref["id"]: tuple(ref["center_mm"]) for ref in schedule["locators"]}
    expected_labels = {ref["id"]: f"{ref['id']} / {ref['item_id']}" for ref in schedule["locators"]}
    if points != expected or labels != expected_labels or len(layer) != 2 * len(expected):
        raise InputError("采购标注 DXF 中的逐件锚点与清单不一致。")


def write_locator(root, project, layout, schedule, dxf_source):
    """Write a purchaser map and editable annotated copy without modifying the bundle."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if any((root / filename).exists() for filename in (PNG_NAME, DXF_NAME)):
        raise InputError("采购定位图目标已存在，请使用新的输出目录。")
    with TemporaryDirectory(prefix=".opc-locator-", dir=root) as temporary:
        staging = Path(temporary)
        export_annotated_dxf(Path(dxf_source), staging / DXF_NAME, project, schedule)
        locator_image(project, layout, schedule).save(staging / PNG_NAME)
        for filename in (PNG_NAME, DXF_NAME):
            (staging / filename).replace(root / filename)
    return {"png": PNG_NAME, "dxf": DXF_NAME}
