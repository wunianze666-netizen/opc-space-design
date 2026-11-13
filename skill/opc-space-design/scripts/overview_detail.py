"""Illustrate selected items together at their verified relative positions."""

from math import ceil

from PIL import Image, ImageDraw
from shapely import union_all
from shapely.geometry import Polygon, box

from layout import components
from model import InputError, label, number, object_fields
from visual import BG, INK, MUTED, PALETTES, axon_frame, font, rgb, scene, shorten, surface_triangles


DETAIL_NAME = "配套局部.png"


def detail_views(rows, items):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 6:
        raise InputError("配套局部须为 1 至 6 项的列表")
    selectable = {item["id"] for item in items if item["category"] in {"furniture", "interior"}}
    names = set()
    result = []
    for row in rows:
        object_fields(row, "name refs angle note", "配套局部")
        name = label(row.get("name"), "配套局部名称")
        refs = row.get("refs")
        if name in names:
            raise InputError(f"配套局部名称重复：{name}")
        if (not isinstance(refs, list) or not 1 <= len(refs) <= 12
                or any(not isinstance(ref, str) or ref not in selectable for ref in refs)
                or len(set(refs)) != len(refs)):
            raise InputError(f"配套局部 {name} 须引用 1 至 12 个本版家具或装修构件编号")
        angle = number(row.get("angle", 225), f"{name} 视角", 0, 360)
        note = label(row["note"], f"{name} 说明") if row.get("note") else ""
        result.append({"name": name, "refs": refs, "angle": angle % 360, "note": note})
        names.add(name)
    return result


def detail_board(project, layout, data):
    views = data["detail_views"]
    items = {item["id"]: item for item in data["items"]}
    all_owners = []
    faces = scene(project, layout["placements"], owners=all_owners)
    palette = PALETTES[project.req["style"]]
    finishes = [element for element in (project.req["interior"] or {}).get("elements", [])
                if element["kind"] == "floor_finish"]
    finished = union_all([element["footprint"] for element in finishes])
    columns = min(3, len(views))
    board = Image.new("RGB", (56 + columns * 686, 150 + ceil(len(views) / columns) * 432), BG)
    draw = ImageDraw.Draw(board)
    draw.text((38, 29), "日常配套 · 局部三维", font=font(44, True), fill=INK)
    draw.text((40, 92), "按所选构件的实际位置和尺寸放大。编号可在整区总览查找。", font=font(23), fill=MUTED)
    for index, view in enumerate(views):
        x, y = 38 + (index % columns) * 686, 143 + (index // columns) * 432
        draw.rounded_rectangle((x, y, x + 666, y + 414), 14, fill="#fffefa", outline="#d5d9ce", width=2)
        draw.text((x + 19, y + 13), shorten(draw, view["name"], font(26, True), 620), font=font(26, True), fill=INK)
        ids = set(view["refs"])
        shapes = [Polygon(items[ref]["polygon"]) for ref in ids]
        x0, y0, x1, y1 = union_all(shapes).bounds
        region = project.outline.intersection(box(x0 - 380, y0 - 380, x1 + 380, y1 + 380))
        drawn = []
        owners = []
        floor = [(region.difference(finished), palette["floor"])]
        floor.extend((region.intersection(element["footprint"]), element["color"]) for element in finishes)
        for shape, color in floor:
            for section in components(shape):
                for points in surface_triangles(section):
                    drawn.append(([(px, py, 0) for px, py in points], rgb(color)))
                    owners.append(("floor", 0))
        for face, owner in zip(faces, all_owners, strict=True):
            if owner is None or owner[0] not in {"furniture", "interior"}:
                continue
            prefix = "F" if owner[0] == "furniture" else "D"
            if f"{prefix}{owner[1] + 1:03d}" in ids:
                drawn.append(face)
                owners.append(owner)
        frame = axon_frame(project, drawn, owners, view["angle"], size=(624, 262))
        board.paste(frame, (x + 21, y + 57))
        for i, group in enumerate((view["refs"][:6], view["refs"][6:])):
            if group:
                draw.text((x + 21, y + 329 + i * 23), "   ".join(group), font=font(17, True), fill=INK)
        if view["note"]:
            draw.text((x + 21, y + 380), shorten(draw, view["note"], font(18), 621), font=font(18), fill=MUTED)
    return board
