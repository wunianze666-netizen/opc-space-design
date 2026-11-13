"""Architectural plan boards and local geometry-driven axonometric views."""

import os
from functools import lru_cache
from math import cos, floor, hypot, pi, sin
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from shapely import constrained_delaunay_triangles, union_all
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient

from layout import components, footprint, pieces, strategy_text
from model import SERVICE_FURNITURE, InputError, as_ring


BG = "#f5f4ee"
INK = "#253630"
MUTED = "#5d6f65"
ACCENT = "#337261"
VOID_FILL = "#e8eeee"
VOID_EDGE = "#647880"
WORK_GROUP_COLORS = (("#dce9ed", "#7999a5"), ("#e4eddf", "#7e9975"), ("#efe8da", "#ac9371"))
SLAB_MM = 155
EXTERIOR_WALL_MM = 105
EXTERIOR_CUT_MM = 115
DEFAULT_CEILING_MM = 2700
PALETTES = {
    "自然木色": {"floor": "#d9c5a9", "desk": "#c9945d", "chair": "#638073", "wall": "#e9e5da", "soft": "#799589"},
    "冷灰极简": {"floor": "#d0d1cf", "desk": "#9fadb2", "chair": "#555f69", "wall": "#e9eced", "soft": "#828f9d"},
    "暖白轻商务": {"floor": "#ded1b9", "desk": "#d0b58d", "chair": "#847263", "wall": "#f2e8da", "soft": "#a4876e"},
    "自然疗愈": {
        "floor": "#e5d5bd", "desk": "#bc946c", "chair": "#718b76", "wall": "#f0ebe0", "soft": "#9ab39a",
        "accent": "#416e5d", "frame": "#677468", "case": "#cbc9ad", "leaf": "#527856",
        "sheet_floor": "#fdfaf1", "floor_pattern": "light_oak",
    },
    "工业创客": {
        "floor": "#bcbcb5", "desk": "#bf9365", "chair": "#4c5d61", "wall": "#d8d8ce", "soft": "#596d72",
        "accent": "#824a32", "frame": "#4a5150", "case": "#969e97", "leaf": "#587459",
        "sheet_floor": "#f8f7f3", "floor_pattern": "concrete",
    },
    "当代展廊": {
        "floor": "#e1dfd7", "desk": "#c8b79d", "chair": "#586786", "wall": "#f3f1e8", "soft": "#929cb0",
        "accent": "#435773", "frame": "#5f646d", "case": "#d8d8cf", "leaf": "#58745b",
        "sheet_floor": "#fdfcf8", "floor_pattern": "stone",
    },
    "科技感": {
        "floor": "#c4cfd5", "desk": "#a5b9c5", "chair": "#3c5273", "wall": "#e4ecf1", "soft": "#607594",
        "accent": "#247489", "frame": "#405560", "case": "#a2b7bd", "leaf": "#42775e",
        "sheet_floor": "#f1f7f9", "floor_pattern": "concrete",
    },
    "石墨科技": {
        "floor": "#62605a", "desk": "#b9b5a6", "chair": "#363834", "wall": "#dad7cb", "soft": "#545551",
        "accent": "#9f6a4d", "frame": "#3c3d38", "case": "#555551", "leaf": "#536653",
        "sheet_floor": "#f6f2e9", "floor_pattern": "concrete",
    },
}
PALETTES["简约"] = PALETTES["冷灰极简"]
PALETTES["绿化"] = PALETTES["自然疗愈"]


def font_path(bold=False):
    root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    paths = [os.environ.get("OPC_FONT_BOLD" if bold else "OPC_FONT"), os.environ.get("OPC_FONT"),
             root / ("msyhbd.ttc" if bold else "msyh.ttc"),
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
             "/System/Library/Fonts/PingFang.ttc"]
    for path in paths:
        if path and Path(path).is_file():
            return str(path)
    raise InputError("需要中文字体。可将 OPC_FONT 指向已安装的中文 TTF/TTC 字体")


@lru_cache(maxsize=32)
def font(size, bold=False):
    return ImageFont.truetype(font_path(bold), size)


def shorten(draw, text, face, width):
    if draw.textlength(text, font=face) <= width:
        return text
    while text and draw.textlength(text + "…", font=face) > width:
        text = text[:-1]
    return text + "…"


def piece_color(kind, palette):
    if kind in ("desk", "coffee_table", "dining", "counter", "workshop", "workbench", "wardrobe") or kind.startswith("meeting_table"):
        return palette["desk"]
    if kind in ("office_chair", "meeting_chair", "dining_chair"):
        return palette["chair"]
    if kind in ("sofa", "screen", "bed", "focus_pod"):
        return palette["soft"]
    if kind in ("planter", "potted_plant"):
        return palette.get("leaf", "#64856a")
    if kind in ("whiteboard", "gallery"):
        return palette["wall"]
    return palette.get("case", "#bfc7b8")


def heading(image, title, subtitle, side, accent=ACCENT):
    d = ImageDraw.Draw(image)
    d.text((57, 42), shorten(d, title, font(41, True), image.width - 370), font=font(41, True), fill=INK)
    d.text((59, 103), shorten(d, subtitle, font(20), image.width - 240), font=font(20), fill=MUTED)
    d.text((image.width - 57, 57), side, anchor="ra", font=font(23, True), fill=accent)
    d.line((57, 141, image.width - 57, 141), fill="#c3c9ba", width=2)
    return d


def local_patch(corners, x, y, width, depth):
    """Rectangle in a furniture footprint's local coordinates."""
    a, b, _, d = corners
    return [tuple(a + (b - a) * u + (d - a) * v) for u, v in
            [(x, y), (x + width, y), (x + width, y + depth), (x, y + depth)]]


def piece_corners(obj):
    corners = np.asarray(obj["polygon"], dtype=float)
    a, b, _, d = corners
    if obj["kind"] == "whiteboard" and np.linalg.norm(b - a) < np.linalg.norm(d - a):
        # A workshop board lies beyond the table in local +x. Its long
        # display face follows local y and faces the table (local -x).
        return corners[[3, 0, 1, 2]]
    return corners


def tint(color, multiplier=1, shift=0):
    return "#" + "".join(f"{max(0, min(255, round(channel * multiplier + shift))):02x}"
                         for channel in rgb(color))


def planter_clusters(corners):
    a, b, _, d = corners
    if np.linalg.norm(b - a) >= np.linalg.norm(d - a):
        return [(.21, .5, .135, .27), (.5, .5, .15, .31), (.79, .5, .135, .27)]
    return [(.5, .21, .27, .135), (.5, .5, .31, .15), (.5, .79, .27, .135)]


def canopy(corners, x, y, rx, ry, scale=1, sides=6):
    a, b, _, d = corners
    return [tuple(a + (b - a) * (x + rx * scale * cos(i * 2 * pi / sides))
                  + (d - a) * (y + ry * scale * sin(i * 2 * pi / sides))) for i in range(sides)]


SORT_COLORS = ("#648b8f", "#85926e", "#a8795b")


def plant_leaves():
    leaves = ((.20, .40, .38, .48), (2.68, .39, .42, .54), (4.59, .37, .48, .61),
              (1.44, .40, .56, .68), (3.52, .36, .60, .75), (5.62, .34, .65, .80),
              (.68, .30, .73, .88), (2.48, .30, .78, .92), (4.32, .25, .82, .96),
              (6.02, .18, .88, 1.0))
    for angle, length, bottom, top in leaves:
        x, y = cos(angle) * length, sin(angle) * length
        sx, sy = -.22 * y, .22 * x
        middle = (.5 + x * .48, .5 + y * .48, bottom + (top - bottom) * .67)
        outline = ((.5, .5, bottom),
                   (middle[0] + sx, middle[1] + sy, middle[2]),
                   (.5 + x, .5 + y, top),
                   (middle[0] - sx, middle[1] - sy, middle[2]))
        yield outline, (middle[0], middle[1], min(1, middle[2] + .026))


def plant_geometry(corners, z, height, color, prism, add):
    lower = canopy(corners, .5, .5, .28, .28, sides=12)
    rim = canopy(corners, .5, .5, .38, .38, sides=12)
    rim_bottom = z + height * .205
    for i, a in enumerate(lower):
        nxt = (i + 1) % len(lower)
        add([(*a, z), (*lower[nxt], z), (*rim[nxt], rim_bottom), (*rim[i], rim_bottom)], "#b28365")
    add([(*point, z) for point in reversed(lower)], "#94674f")
    prism(rim, rim_bottom, z + height * .245, "#c59677")
    prism(canopy(corners, .5, .5, .32, .32, sides=12), z + height * .245, z + height * .251, "#6b5b43")
    prism(local_patch(corners, .48, .48, .04, .04), z + height * .251, z + height * .92, "#6e634a")
    a, b, _, d = corners

    def xyz(point):
        u, v, level = point
        return (*tuple(a + (b - a) * u + (d - a) * v), z + height * level)

    for index, (outline, ridge) in enumerate(plant_leaves()):
        for side, point in enumerate(outline):
            paint = tint(color, .89 if side < 2 else 1.05, (index % 3) * 9)
            add([xyz(ridge), xyz(point), xyz(outline[(side + 1) % 4])], paint)


def utility_geometry(kind, patch, z, height, palette, prism, finish=None):
    case = finish if finish and kind == "water_station" else palette.get("case", "#bfc7b8")
    wood = finish if finish and kind == "entry_organizer" else palette["desk"]
    frame = finish if finish and kind == "recycling_station" else palette.get("frame", "#626a62")

    def panel(x, y, width, depth, low, high, paint):
        prism(patch(x, y, width, depth), z + height * low, z + height * high, paint)

    if kind == "water_station":
        panel(0, .02, 1, .96, 0, .10, frame)
        panel(.025, .055, .95, .925, .10, .47, case)
        panel(.06, .028, .86, .026, .135, .435, tint(case, 1, 13))
        panel(.02, .80, .96, .18, .47, .92, tint(frame, .86))
        panel(.025, .035, .075, .765, .47, .92, case)
        panel(.67, .035, .055, .765, .47, .92, case)
        panel(.91, .035, .065, .765, .47, .92, case)
        panel(.10, .13, .57, .59, .47, .505, "#86979a")
        panel(.36, .63, .07, .10, .68, .79, "#b4c6c6")
        panel(.36, .43, .07, .20, .755, .79, "#b4c6c6")
        for level in (.54, .71):
            panel(.725, .14, .185, .61, level, level + .022, wood)
            for x in (.747, .825):
                panel(x, .28, .054, .14, level + .022, level + .105, "#f3eee1")
        panel(.025, .035, .95, .945, .92, 1, tint(case, 1, 12))
    elif kind == "recycling_station":
        panel(0, 0, 1, 1, 0, .08, frame)
        panel(.015, .06, .97, .925, .08, .90, frame)
        for i, paint in enumerate(SORT_COLORS):
            x = .025 + i * .327
            panel(x, .012, .294, .048, .12, .88, tint(paint, 1, 12))
            panel(x + .082, .001, .13, .012, .68, .728, "#eeede0")
            panel(x, .035, .294, .93, .90, .96, paint)
            panel(x, .035, .294, .37, .96, 1, paint)
            panel(x, .615, .294, .35, .96, 1, paint)
            for u in (0, .221):
                panel(x + u, .405, .073, .21, .96, 1, paint)
            panel(x + .075, .41, .144, .20, .956, .9605, "#394143")
    elif kind in SERVICE_FURNITURE:
        color = finish or case
        panel(.04, .06, .92, .88, 0, .065, frame)
        if kind == "waste_bin":
            panel(.04, .05, .92, .90, .065, .90, tint(color, .86))
            panel(.12, .025, .76, .032, .70, .77, "#e9e2d5")
            panel(0, 0, 1, 1, .90, .95, frame)
            panel(.07, .07, .86, .15, .95, 1, frame)
            panel(.07, .70, .86, .23, .95, 1, frame)
            for x in (.07, .81):
                panel(x, .22, .12, .48, .95, 1, frame)
        elif kind == "print_station":
            panel(.02, .05, .96, .92, .065, .65, color)
            for x in (.07, .525):
                panel(x, .017, .407, .038, .10, .59, tint(color, 1.03))
                panel(x + .14, 0, .13, .019, .50, .53, frame)
            panel(0, 0, 1, 1, .65, .70, wood)
            panel(.22, .22, .64, .70, .70, .94, "#d1d3cc")
            panel(.22, .20, .64, .73, .94, 1, "#6c756d")
            panel(.32, .19, .36, .024, .78, .825, "#343b37")
            panel(.26, .09, .30, .11, .70, .72, "#e7e7db")
            panel(.73, .16, .11, .041, .855, .91, "#536c66")
        elif kind == "charging_station":
            panel(.025, .055, .95, .91, .065, .82, color)
            panel(0, 0, 1, 1, .82, .87, wood)
            panel(.025, .76, .95, .19, .87, 1, frame)
            for i in range(3):
                x = .11 + i * .30
                panel(x, .035, .18, .035, .23, .71, tint(color, 1.05))
                panel(x, .745, .16, .02, .90, .96, "#4e5a52")
                panel(x + .05, .73, .054, .017, .919, .942, "#d5c7a7")
        else:
            panel(.015, .05, .97, .92, .065, 1, color)
            columns, rows = (3, 3) if kind == "locker_bank" else (2, 1)
            for column in range(columns):
                for row in range(rows):
                    x, low = .025 + .95 * column / columns, .095 + .87 * row / rows
                    w, h = .92 / columns, .84 / rows
                    panel(x, .014, w, .039, low, low + h, tint(color, 1.03))
                    panel(x + w * .78, 0, w * .087, .018, low + h * .42, low + h * .58, frame)
                    if kind == "cleaning_cabinet":
                        for i in range(3):
                            panel(x + .10, .006, .22, .011, .765 + i * .05, .777 + i * .05, frame)
    else:
        panel(0, .03, 1, .94, 0, .054, frame)
        panel(.045, .79, .64, .175, .054, 1, tint(wood, .86))
        panel(.045, .075, .055, .715, .054, .48, frame)
        panel(.615, .075, .07, .715, .054, .48, frame)
        panel(.10, .11, .515, .68, .092, .133, tint(wood, 1, 14))
        panel(.045, .035, .64, .755, .455, .50, wood)
        panel(.115, .10, .37, .27, .50, .516, "#a09178")
        for x in (.15, .31, .47):
            panel(x, .62, .055, .17, .82, .855, frame)
            panel(x, .60, .055, .046, .852, .884, frame)
        panel(.73, .12, .227, .72, .054, .118, frame)
        for x in (.73, .921):
            panel(x, .12, .036, .72, .118, .60, tint(case, .85))
        panel(.766, .806, .155, .034, .118, .60, tint(case, .85))
        panel(.766, .12, .155, .028, .118, .49, tint(case, 1, 11))


def draw_plan_piece(draw, obj, project_points, palette):
    kind = obj["kind"]
    outline = "#596454"
    color = obj.get("color_hex") or piece_color(kind, palette)
    draw.polygon(project_points(obj["polygon"]), fill=color, outline=outline, width=2)
    if kind not in ("focus_pod", "workshop", "workbench", "whiteboard", "planter", "gallery",
                    "water_station", "recycling_station", "entry_organizer", "potted_plant", "wardrobe") and kind not in SERVICE_FURNITURE:
        return
    corners = piece_corners(obj)

    def panel(x, y, width, depth, fill, border=None):
        draw.polygon(project_points(local_patch(corners, x, y, width, depth)),
                     fill=fill, outline=border)

    frame = palette.get("frame", outline)
    accent = palette.get("accent", ACCENT)
    if kind == "focus_pod":
        panel(.09, .04, .82, .87, palette["wall"])
        panel(.04, .06, .08, .9, frame)
        panel(.88, .06, .08, .9, frame)
        panel(.12, .88, .76, .08, frame)
        panel(.18, .13, .64, .055, tint(color, .92))
    elif kind in ("workshop", "workbench"):
        panel(.055, .07, .89, .13, frame)
        panel(.1, .27, .8, .6, tint(color, 1, 16))
        panel(.12, .36, .035, .39, accent)
    elif kind == "whiteboard":
        panel(.06, .17, .88, .64, "#fbfaf3", frame)
        panel(.22, .28, .56, .10, accent)
    elif kind == "gallery":
        panel(.06, .14, .88, .70, palette.get("case", "#d2cfc4"), frame)
        panel(.12, .36, .76, .25, accent)
    elif kind == "wardrobe":
        panel(.02, .02, .96, .15, tint(color, .84), frame)
        panel(.49, .02, .02, .15, frame)
    elif kind == "planter":
        panel(.05, .07, .9, .86, "#79705d")
        for x, y, rx, ry in planter_clusters(corners):
            draw.polygon(project_points(canopy(corners, x, y, rx, ry)), fill=tint(color, 1.2))
            draw.polygon(project_points(canopy(corners, x, y, rx, ry, .5)), fill=color)
    elif kind == "water_station":
        panel(.09, .12, .59, .69, "#536e76")
        panel(.16, .19, .45, .25, "#92adb3")
        panel(.73, .17, .17, .55, "#ded5bd")
        panel(.78, .29, .075, .26, "#fbf3df")
    elif kind == "recycling_station":
        for i, paint in enumerate(SORT_COLORS):
            x = .03 + i * .325
            panel(x, .06, .29, .87, paint, frame)
            panel(x + .077, .40, .139, .21, "#394143")
    elif kind == "entry_organizer":
        panel(.05, .12, .60, .69, palette["desk"], frame)
        panel(.065, .77, .58, .13, frame)
        panel(.73, .17, .20, .69, tint(color, .81), frame)
        panel(.77, .25, .12, .48, "#e7e0d3")
    elif kind == "waste_bin":
        panel(.10, .15, .80, .70, frame)
        panel(.29, .33, .42, .34, "#353e37")
    elif kind == "print_station":
        panel(.22, .20, .64, .73, "#6c756d", frame)
        panel(.26, .07, .34, .17, "#eeeee6")
    elif kind in SERVICE_FURNITURE:
        panel(.045, .055, .91, .16, frame)
    elif kind == "potted_plant":
        draw.polygon(project_points(canopy(corners, .5, .5, .36, .36, sides=12)),
                     fill="#b28365", outline="#8d674f")
        a, b, _, d = corners
        for index, (outline, _) in enumerate(plant_leaves()):
            points = [tuple(a + (b - a) * x + (d - a) * y) for x, y, _ in outline]
            draw.polygon(project_points(points), fill=tint(color, .95, (index % 3) * 12))


def plan_area_label(draw, text, shape, xy, scale, fill, ink):
    point = shape.representative_point()
    typeface = font(17)
    text_width = draw.textlength(text, font=typeface)
    wx, hy = (text_width / 2 + 9) / scale, 14 / scale
    if not shape.covers(box(point.x - wx, point.y - hy, point.x + wx, point.y + hy)):
        return False
    x, y = xy(point.x, point.y)
    draw.rounded_rectangle((x - text_width / 2 - 8, y - 13, x + text_width / 2 + 8, y + 13), radius=5, fill=fill)
    draw.text((x, y), text, anchor="mm", font=typeface, fill=ink)
    return True


def plan_voids(image, voids, xy, poly, scale):
    mask = Image.new("1", image.size)
    mask_draw = ImageDraw.Draw(mask)
    overlay = Image.new("RGB", image.size, VOID_FILL)
    hatch = ImageDraw.Draw(overlay)
    for x in range(-image.height, image.width, 13):
        hatch.line((x, image.height, x + image.height, 0), fill="#a9b9bc", width=1)
    for _, shape in voids:
        mask_draw.polygon(poly(as_ring(shape)), fill=1)
    image.paste(overlay, (0, 0), mask)
    draw = ImageDraw.Draw(image)
    for name, shape in voids:
        ring = poly(as_ring(shape))
        draw.line(ring + ring[:1], fill=VOID_EDGE, width=3, joint="curve")
        plan_area_label(draw, name, shape, xy, scale, "#f8faf9", "#39505a")


def plan_interior(draw, elements, poly, *, floor):
    if not elements:
        return

    for element in elements:
        kind, shape = element["kind"], element["footprint"]
        if (kind == "floor_finish") != floor:
            continue
        paint = element["color"]
        ring = poly(as_ring(shape))
        if floor:
            draw.polygon(ring, fill=tint(paint, .34, 157), outline=tint(paint, .83), width=2)
        elif element.get("segment"):
            draw.polygon(ring, fill=paint)
            draw.line(poly(element["segment"]), fill=tint(paint, .70), width=4)
        else:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                length = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** .5
                if not length:
                    continue
                for start in range(0, int(length), 11):
                    t, end = start / length, min(1, (start + 6) / length)
                    draw.line(((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t),
                               (a[0] + (b[0] - a[0]) * end, a[1] + (b[1] - a[1]) * end)),
                              fill="#6f7886", width=2)


def plan_eye_marker(draw, view, xy):
    x, y = xy(*view["eye_mm"][:2])
    tx, ty = xy(*view["target_mm"][:2])
    length = hypot(tx - x, ty - y)
    if length > 1e-6:
        dx, dy = (tx - x) / length, (ty - y) / length
        visible_length = min(length, 49)
        tip = (x + dx * visible_length, y + dy * visible_length)
        base = (tip[0] - dx * 9, tip[1] - dy * 9)
        draw.line((x, y, *tip), fill="#fffdf7", width=6)
        draw.line((x, y, *tip), fill="#315a70", width=3)
        draw.polygon([tip, (base[0] - dy * 4, base[1] + dx * 4),
                      (base[0] + dy * 4, base[1] - dx * 4)], fill="#315a70")
    draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#315a70", outline="#fffdf7", width=2)


def plan_image(project, placements, label, strategy=None):
    image = Image.new("RGB", (1560, 1100), BG)
    text = strategy_text(project, strategy)[0] if strategy else "输入几何确认"
    style = project.req["style"]
    palette = PALETTES[style]
    voids = getattr(project, "voids", ()) or [("无楼板区", Polygon(ring)) for ring in project.outline.interiors]
    groups = project.req.get("work_groups", {})
    interior = project.req.get("interior")
    elements = interior["elements"] if interior else []
    eye = interior["view"] if interior else None
    area = f"{project.outline.area / 1e6:.1f} m²"
    if voids:
        area = "净楼板 " + area
    subtitle = f"{text}  /  {area}  /  项目通行净宽 {project.req['aisle_mm']} mm"
    if placements and ("floor_pattern" in palette or style == "简约"):
        subtitle += f"  /  {style}"
    d = heading(image, project.title, subtitle, label, palette.get("accent", ACCENT))
    x0, y0, x1, y1 = project.outline.bounds
    scale = min(1220 / (x1 - x0), 655 / (y1 - y0))
    left = 780 - (x1 - x0) * scale / 2
    bottom = 200 + (655 + (y1 - y0) * scale) / 2

    def xy(x, y):
        return left + (x - x0) * scale, bottom - (y - y0) * scale

    def poly(shape):
        return [xy(x, y) for x, y in shape]

    boundary = poly(as_ring(project.outline))
    d.polygon([(x + 10, y + 14) for x, y in boundary], fill="#e1e1d5")
    d.polygon(boundary, fill=palette.get("sheet_floor", "#fffdf7"), outline=INK, width=6)
    plan_interior(d, elements, poly, floor=True)
    for index, group in enumerate(groups.values()):
        fill, stroke = WORK_GROUP_COLORS[index % len(WORK_GROUP_COLORS)]
        if any(element["kind"] == "floor_finish" for element in elements):
            ImageDraw.Draw(image, "RGBA").polygon(poly(as_ring(group["polygon"])), fill=(*rgb(fill), 120),
                                                   outline=(*rgb(stroke), 255), width=2)
        else:
            d.polygon(poly(as_ring(group["polygon"])), fill=fill, outline=stroke, width=2)
    for name, kind, shape in project.clear:
        fill, stroke = ("#efd4d0", "#b87a71") if kind == "no_walk" else ("#f5e5cb", "#bd8a45")
        d.polygon(poly(as_ring(shape)), fill=fill, outline=stroke, width=2)
        if kind in ("reserved", "no_walk") and shape.equals(shape.envelope):
            a, b, c, e = shape.bounds
            face = font(18)
            if (c - a) * scale > d.textlength(name, font=face) + 20 and (e - b) * scale > 42:
                d.text(xy((a + c) / 2, (b + e) / 2), name, font=face, fill="#77572d", anchor="mm")
    for exit_ in project.exits:
        clipped = exit_.vestibule(project.req["aisle_mm"]).intersection(project.outline)
        if clipped.geom_type == "Polygon":
            d.polygon(poly(as_ring(clipped)), fill="#e2efe3")
    for line, _, shape in project.req["clear_routes"].values():
        d.polygon(poly(as_ring(shape)), fill="#e0edf2", outline="#82a6b6", width=2)
        d.line(poly(list(line.coords)), fill="#82a6b6", width=2)
    for _, _, shape in project.fixed:
        d.polygon(poly(as_ring(shape)), fill="#52635a", outline=INK, width=2)
    for placement in placements:
        envelope = poly(as_ring(footprint(placement, project)))
        d.polygon(envelope, fill="#edf0e6", outline="#c1cbbd", width=1)
    for obj in pieces(placements, project):
        draw_plan_piece(d, obj, poly, palette)
    for index, (name, group) in enumerate(groups.items()):
        fill, stroke = WORK_GROUP_COLORS[index % len(WORK_GROUP_COLORS)]
        shape = group["polygon"]
        # Use the edge of the zone to keep its workpoints visible under the label.
        cutoff = shape.bounds[3] - 31 / scale
        edge = shape.intersection(box(x0, cutoff, x1, y1))
        ink = tint(stroke, .68)
        if edge.geom_type == "Polygon" and not edge.is_empty:
            if plan_area_label(d, f"{name} · {group['desks']}席", edge, xy, scale, fill, ink):
                continue
            if plan_area_label(d, name, edge, xy, scale, fill, ink):
                continue
        plan_area_label(d, name, shape, xy, scale, fill, ink)
    approaches = project.req["required_approaches"]
    for name, (point, _) in approaches.items():
        a, b = xy(point.x, point.y)
        d.ellipse((a - 5, b - 5, a + 5, b + 5), fill="#795882", outline="#ffffff", width=1)
        if len(approaches) <= 4:
            face = font(16)
            caption = shorten(d, name, face, 160)
            right = a + 17 + d.textlength(caption, font=face)
            d.rounded_rectangle((a + 9, b - 13, right, b + 13), radius=4, fill="#f8f4f8")
            d.text((a + 13, b), caption, font=face, fill="#65436e", anchor="lm")
    if voids:
        plan_voids(image, voids, xy, poly, scale)
    d.line(boundary + boundary[:1], fill=INK, width=5, joint="curve")
    plan_interior(d, elements, poly, floor=False)
    if eye:
        plan_eye_marker(d, eye, xy)
    for op, color in [(op, "#42837e") for op in project.windows] + [(op, "#448953") for op in project.exits]:
        d.line(poly(list(op.line.coords)), fill=BG, width=10)
        d.line(poly(list(op.line.coords)), fill=color, width=6)
    for idx, op in enumerate(project.exits, 1):
        p = op.point(-340)
        a, b = xy(p.x, p.y)
        txt = f"出口{idx} · {op.line.length / 1000:g} m"
        d.rounded_rectangle((a - 63, b - 12, a + 63, b + 13), radius=4, fill="#dceade")
        d.text((a, b), txt, anchor="mm", font=font(16), fill="#295e40")
    right, top = xy(x1, y1)
    dim_y = bottom + 62
    dim_x = left - 55
    d.line((left, dim_y, right, dim_y), fill=MUTED, width=2)
    d.line((dim_x, top, dim_x, bottom), fill=MUTED, width=2)
    for x in (left, right):
        d.line((x - 5, dim_y + 6, x + 5, dim_y - 6), fill=MUTED, width=2)
    for y in (top, bottom):
        d.line((dim_x - 5, y + 6, dim_x + 5, y - 6), fill=MUTED, width=2)
    d.text(((left + right) / 2, dim_y + 13), f"{(x1-x0)/1000:.2f} m", anchor="mt", font=font(21), fill=MUTED)
    d.text((dim_x, (top + bottom) / 2), f"{(y1-y0)/1000:.2f} m", anchor="rm", font=font(20), fill=MUTED)
    d.line((57, 990, 1503, 990), fill="#c3c9ba", width=2)
    for x, color, text in [(59, palette["desk"], "家具"), (202, "#edf0e6", "使用空间"),
                           (390, "#52635a", "固定结构"), (579, "#f5e5cb", "禁占区"),
                           (747, "#448953", "已确认出口"), (944, "#42837e", "窗")]:
        d.rectangle((x, 1020, x + 18, 1038), fill=color, outline="#a3aea0")
        d.text((x + 29, 1015), text, font=font(20), fill=MUTED)
    labels = []
    if project.req["clear_routes"]:
        labels.append("浅蓝带为指定通道")
    if project.req["required_approaches"]:
        labels.append("紫点为门前检测点")
    if any(kind == "no_walk" for _, kind, _ in project.clear):
        labels.append("淡红区禁止通行")
    if voids:
        labels.append("斜纹为无楼板区")
    if groups:
        labels.append("彩色轮廓为工位分区")
    if elements:
        labels.append("墙边色带和虚线表示室内饰面、吊顶设施")
    if eye:
        labels.append("箭头为人视方向")
    if labels:
        d.text((59, 1054), "  /  ".join(labels), font=font(16), fill=MUTED)
    bar = min(2000, floor((x1 - x0) / 1000) * 500) * scale
    if bar > 0:
        d.line((1500 - bar, 1037, 1500, 1037), fill=INK, width=4)
        d.text((1500, 1044), f"{bar / scale / 1000:g} m", anchor="ra", font=font(16), fill=MUTED)
    return image


def rgb(color):
    return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))


def scene_height(project):
    if project.height is not None:
        return project.height
    openings = [op.head_mm for op in project.exits + project.windows if op.head_mm is not None]
    return max(DEFAULT_CEILING_MM, *openings) if openings else DEFAULT_CEILING_MM


def surface_triangles(shape):
    return [list(orient(triangle, sign=1).exterior.coords)[:-1]
            for triangle in constrained_delaunay_triangles(shape).geoms]


def wall_finish_geometry(element, color, prism, azimuth, *, flat_art=False):
    a, b = map(np.asarray, element["segment"])
    along = b - a
    length = np.linalg.norm(along)
    tangent = along / length
    normal = np.array([-tangent[1], tangent[0]])
    shape = element["footprint"]
    if np.dot(np.asarray(shape.centroid.coords[0]) - a, normal) < 0:
        normal *= -1
    if azimuth is not None and np.dot(normal, [cos(azimuth), sin(azimuth)]) < -.08:
        return
    inward = normal * max(np.dot(np.asarray(point) - a, normal) for point in shape.exterior.coords)
    bottom = element["bottom_mm"]
    height = element["top_mm"] - bottom

    def panel(u, width, v, depth, low, high, paint):
        coords = [tuple(a + along * x + inward * y)
                  for x, y in [(u, v), (u + width, v), (u + width, v + depth), (u, v + depth)]]
        if along[0] * inward[1] < along[1] * inward[0]:
            coords.reverse()
        prism(coords, bottom + height * low, bottom + height * high, paint)

    kind = element["kind"]
    if kind == "slats":
        panel(0, 1, 0, .26, 0, 1, tint(color, .47))
        count = max(3, min(70, round(length / 105)))
        for i in range(count):
            panel((i + .17) / count, .62 / count, .26, .74, 0, 1, color if i % 4 else tint(color, .91))
    elif kind == "acoustic":
        panel(0, 1, 0, .22, 0, 1, tint(color, .67))
        count = max(1, min(18, round(length / 600)))
        for i in range(count):
            panel((i + .014) / count, .972 / count, .22, .78, .012, .988,
                  color if i % 2 else tint(color, 1, 9))
    elif kind == "art":
        if flat_art:
            panel(0, 1, 0, 1, 0, 1, color)
            return
        panel(0, 1, 0, .64, 0, 1, "#384148")
        panel(.038, .924, .64, .18, .055, .945, "#e6dfd3")
        panel(.11, .57, .82, .145, .21, .72, color)
        panel(.54, .35, .83, .155, .39, .82, tint(color, .58))
        panel(.20, .62, .95, .05, .28, .35, "#bf926b")
    elif kind == "notice_board":
        panel(0, 1, 0, .61, 0, 1, "#6d5b49")
        panel(.027, .946, .61, .19, .045, .955, color)
        cards = ((.09, .21, .25, .69, "#fff3d8"), (.36, .23, .47, .84, "#f4ebdc"),
                 (.65, .22, .32, .77, "#e8ead9"), (.40, .17, .17, .36, "#faf0df"))
        for x, width, low, high, paint in cards:
            panel(x, width, .80, .10, low, high, paint)
            panel(x + width * .48, width * .065, .90, .09, high - .047, high - .015, "#955f49")
            for row in (.33, .48, .63):
                baseline = low + (high - low) * row
                panel(x + width * .15, width * .66, .90, .015, baseline, baseline + .012, "#b9ae99")
    else:
        panel(0, 1, 0, .29, 0, 1, color)
        panel(.015, .032, .29, .71, 0, 1, tint(color, .57))
        panel(.953, .032, .29, .71, 0, 1, tint(color, .57))
        for level in (.23, .52, .81):
            panel(.046, .908, .29, .71, level, level + .027, "#b6936d")


def scene(project, placements, azimuth=None, *, view_openings=(), section_height=None, owners=None,
          cover_ceiling=False, textured_indices=()):
    """Build shared geometry with an optional parallel list of face owners."""
    faces = []
    palette = PALETTES[project.req["style"]]
    owner = None

    def add(vertices, color):
        faces.append((vertices, rgb(color)))
        if owners is not None:
            owners.append(owner)

    def face(xy, height, color):
        add([(x, y, height) for x, y in xy], color)

    def prism(xy, bottom, top, color):
        face(xy, top, color)
        face(xy[::-1], bottom, color)
        for a, b in zip(xy, xy[1:] + xy[:1]):
            add([(*a, bottom), (*b, bottom), (*b, top), (*a, top)], color)

    def solid(shape, bottom, top, color):
        for triangle in surface_triangles(shape):
            face(triangle, top, color)
            face(triangle[::-1], bottom, color)
        ring = list(orient(shape, sign=1).exterior.coords)
        for a, b in zip(ring, ring[1:]):
            add([(*a, bottom), (*b, bottom), (*b, top), (*a, top)], color)

    interior = project.req.get("interior")
    elements = interior["elements"] if interior else []
    if elements:
        from interior import MATERIALS

    slab = orient(project.outline, sign=1)
    ring = list(slab.exterior.coords)[:-1]
    tiles = surface_triangles(slab)
    owner = ("floor", 0)
    finishes = [(index, element) for index, element in enumerate(elements) if element["kind"] == "floor_finish"]
    if finishes:
        shapes = [element["footprint"] for _, element in finishes]
        for shape in components(slab.difference(union_all(shapes))):
            for triangle in surface_triangles(shape):
                face(triangle, 0, palette["floor"])
        for index, element in finishes:
            owner = ("interior", index) if index in textured_indices else ("floor", 0)
            for triangle in surface_triangles(element["footprint"]):
                face(triangle, 0, element["color"])
    else:
        for triangle in tiles:
            face(triangle, 0, palette["floor"])
    owner = ("slab", 0)
    for triangle in tiles:
        face(triangle[::-1], -SLAB_MM, "#a1a394")
    for index, edge in enumerate([slab.exterior, *slab.interiors]):
        color = "#a1a394" if index == 0 else VOID_EDGE
        vertices = list(edge.coords)
        for a, b in zip(vertices, vertices[1:]):
            add([(*a, -SLAB_MM), (*b, -SLAB_MM), (*b, 0), (*a, 0)], color)
    owner = None
    height = scene_height(project)
    if cover_ceiling:
        owner = ("ceiling", 0)
        for triangle in surface_triangles(Polygon(slab.exterior)):
            face(triangle[::-1], height, "#edece4")
        owner = None
    window_height = min(height, 3000)
    for index, (_, kind, shape) in enumerate(project.fixed):
        owner = ("fixed", index)
        top = height if kind in ("column", "wall") else min(height, 1550)
        if section_height is not None and kind == "wall":
            top = min(top, section_height)
        ring_fixed = as_ring(orient(shape, sign=1))
        # Top of concave fixed shapes is triangulated.
        for triangle in constrained_delaunay_triangles(shape).geoms:
            face(as_ring(triangle), top, "#aab4a5")
        for a, b in zip(ring_fixed, ring_fixed[1:] + ring_fixed[:1]):
            add([(*a, 0), (*b, 0), (*b, top), (*a, top)], "#aab4a5")

    openings_by_type = ([("exit", op.line, op, i) for i, op in enumerate(project.exits)]
                        + [("window", op.line, op, i) for i, op in enumerate(project.windows)]
                        + [("exit", line, None, None) for line in view_openings])
    for edge_index, (a, b) in enumerate(zip(ring, ring[1:] + ring[:1])):
        edge = LineString([a, b])
        dx, dy = (b[0] - a[0]) / edge.length, (b[1] - a[1]) / edge.length
        cut = azimuth is not None and dy * cos(azimuth) - dx * sin(azimuth) > .08
        breaks = []
        openings = []
        for kind, line, op, index in openings_by_type:
            if edge.buffer(.6).covers(line):
                lo, hi = sorted(edge.project(Point(xy)) for xy in line.coords)
                openings.append((lo, hi, kind, op, index))
        start = 0
        for lo, hi, kind, op, index in sorted(openings, key=lambda row: row[:2]):
            if lo > start:
                breaks.append((start, lo, "wall", None, None))
            breaks.append((lo, hi, kind, op, index))
            start = hi
        if start < edge.length:
            breaks.append((start, edge.length, "wall", None, None))
        for lo, hi, kind, op, index in breaks:
            owner = ("exterior_wall", edge_index)
            x, y = a[0] + lo * dx, a[1] + lo * dy
            u, v = a[0] + hi * dx, a[1] + hi * dy
            px, py = EXTERIOR_WALL_MM * dy, -EXTERIOR_WALL_MM * dx
            poly = [(x, y), (u, v), (u + px, v + py), (x + px, y + py)]
            if kind == "exit":
                head = op.head_mm if op and op.head_mm is not None else 2120
                if not cut and height > head:
                    owner = ("exit_header", index) if op else None
                    prism(poly, head, height, palette["wall"])
            elif cut:
                prism(poly, 0, EXTERIOR_CUT_MM, palette["wall"])
            elif kind == "window":
                if op.sill_mm is None:
                    sill, glass_bottom = window_height * .32, window_height * .33
                    glass_top, head = window_height * .81, window_height * .82
                else:
                    sill = glass_bottom = op.sill_mm
                    head = glass_top = op.head_mm
                if sill > 0:
                    prism(poly, 0, sill, palette["wall"])
                if height > head:
                    prism(poly, head, height, palette["wall"])
                owner = ("window", index)
                prism(poly, glass_bottom, glass_top, "#a3c4c9")
            else:
                prism(poly, 0, height, palette["wall"])

    frame = palette.get("frame", "#626a62")
    accent = palette.get("accent", ACCENT)
    case = palette.get("case", "#bfc7b8")
    for index, obj in enumerate(pieces(placements, project)):
        owner = ("furniture", index)
        kind = obj["kind"]
        corners = piece_corners(obj)

        def patch(x, y, w, h):
            return local_patch(corners, x, y, w, h)

        color = obj.get("color_hex") or piece_color(kind, palette)
        z, top = obj["base"], obj["base"] + obj["height"]
        height = obj["height"]
        if kind in ("desk", "coffee_table", "dining") or kind.startswith("meeting_table"):
            bottom = top - min(65, height * .25)
            prism(patch(0, 0, 1, 1), bottom, top, color)
            for x in (.06, .87):
                for y in (.10, .82):
                    prism(patch(x, y, .06, .08), z, bottom + 1, frame)
        elif kind in ("workshop", "workbench"):
            prism(patch(0, 0, 1, 1), z + height * .88, top, color)
            prism(patch(.07, .13, .86, .74), z + height * .24, z + height * .32, tint(frame, 1, 22))
            for x in (.05, .87):
                for y in (.08, .82):
                    prism(patch(x, y, .08, .09), z, z + height * .88, frame)
            prism(patch(.05, .06, .9, .075), z + height * .78, z + height * .88, accent)
        elif kind in ("office_chair", "meeting_chair", "dining_chair"):
            prism(patch(.08, .06, .84, .84), 405, 495, color)
            prism(patch(.43, .44, .14, .14), 38, 406, "#59645b")
            prism(patch(.12, .40, .76, .24), 17, 49, "#59645b")
            prism(patch(.08, .77 if obj["back"] == "far" else .06, .84, .15), 480, top, color)
        elif kind == "bed":
            prism(patch(0, 0, 1, 1), 0, top * .48, color)
            prism(patch(.02, .02, .96, .96), top * .48, top * .86, "#e8e4d9")
            prism(patch(.03, .04, .94, .61), top * .86, top * .91, color)
            for x in (.08, .52):
                prism(patch(x, .72, .4, .21), top * .86, top, "#f8f6ef")
        elif kind == "sofa":
            prism(patch(0, 0, 1, 1), 55, 410, color)
            prism(patch(0, .73, 1, .27), 410, top, color)
            for x in (0, .89):
                prism(patch(x, 0, .11, .73), 410, 640, color)
            for x in (.12, .51):
                prism(patch(x, .04, .37, .67), 411, 475, color)
        elif kind == "focus_pod":
            prism(patch(0, 0, 1, 1), z, z + height * .025, case)
            prism(patch(.08, .04, .84, .85), z + height * .025, z + height * .03, tint(color, 1, 18))
            for x in (0, .91):
                prism(patch(x, .04, .09, .92), z + height * .025, top, frame)
            prism(patch(.09, .88, .82, .08), z + height * .025, top, color)
            prism(patch(.09, .04, .82, .075), z + height * .92, top, color)
            if azimuth is None:
                prism(patch(.09, .04, .82, .86), z + height * .95, top, frame)
                prism(patch(.10, .052, .80, .013), z + height * .04, z + height * .92, "#a3c4c9")
            for i in range(6):
                prism(patch(.15 + i * .12, .86, .04, .02), z + height * .27,
                      z + height * .77, tint(color, .9))
        elif kind == "whiteboard":
            for x in (.12, .80):
                prism(patch(x, .10, .07, .80), z, z + height * .04, frame)
                prism(patch(x, .45, .07, .15), z + height * .04, z + height * .96, frame)
            prism(patch(.04, .34, .92, .20), z + height * .35, top, frame)
            prism(patch(.065, .29, .87, .05), z + height * .38, z + height * .97, "#fbfaf3")
            prism(patch(.18, .16, .64, .13), z + height * .35, z + height * .38, accent)
        elif kind == "gallery":
            prism(patch(0, .05, 1, .90), z, z + height * .09, case)
            prism(patch(.035, .36, .93, .26), z + height * .09, top, color)
            for x, material in ((.16, accent), (.55, tint(accent, 1, 42))):
                prism(patch(x, .31, .29, .05), z + height * .47, z + height * .82, frame)
                prism(patch(x + .025, .28, .24, .03), z + height * .49, z + height * .8, material)
        elif kind == "planter":
            prism(patch(0, 0, 1, 1), z, z + height * .35, case)
            prism(patch(.055, .055, .89, .89), z + height * .35, z + height * .37, "#79705d")
            for i, (x, y, rx, ry) in enumerate(planter_clusters(corners)):
                prism(patch(x - .018, y - .022, .036, .044), z + height * .37, z + height * .74, "#6f5b47")
                prism(canopy(corners, x, y, rx, ry), z + height * .53,
                      z + height * .78, tint(color, .91, i * 6))
                prism(canopy(corners, x, y, rx, ry, .71), z + height * .78,
                      top - (1 if i == 1 else .08 * height), tint(color, 1.15, i * 5))
        elif kind == "potted_plant":
            plant_geometry(corners, z, height, color, prism, add)
        elif kind == "wardrobe":
            prism(patch(0, .12, 1, .88), z, top, tint(color, .85))
            for i, x in enumerate((.02, .505)):
                panel_color = tint(color, 1.02 if i else .92)
                prism(patch(x, .025, .475, .095), z + height * .045, z + height * .985, panel_color)
                handle = .456 if i == 0 else .536
                prism(patch(handle, .0, .016, .025), z + height * .39, z + height * .61, frame)
        elif kind in ("water_station", "recycling_station", "entry_organizer") or kind in SERVICE_FURNITURE:
            utility_geometry(kind, patch, z, height, palette, prism, obj.get("color_hex"))
        else:
            prism(obj["polygon"], z, top, color)
    for index, element in enumerate(elements):
        kind = element["kind"]
        if kind == "floor_finish":
            continue
        owner = ("interior", index)
        shape = element["footprint"]
        bottom, top = element["bottom_mm"], element["top_mm"]
        paint = element["color"]
        if element.get("segment"):
            wall_finish_geometry(element, paint, prism, azimuth, flat_art=index in textured_indices)
        elif kind == "ceiling_light":
            cut = bottom + (top - bottom) * .33
            x0, y0, x1, y1 = shape.bounds
            diffuser = shape.intersection(box(x0 + (x1 - x0) * .02, y0 + (y1 - y0) * .05,
                                               x1 - (x1 - x0) * .02, y1 - (y1 - y0) * .05))
            housing = "#586168" if element["material"] == "warm_light" else paint
            solid(shape, cut, top, housing)
            for piece in components(diffuser):
                solid(piece, bottom, cut, MATERIALS["warm_light"])
        else:
            solid(shape, bottom, top, paint)
    return faces


def axon_frame(project, faces, owners, angle, *, size=(1350, 770), textures=None):
    azimuth, elevation = angle * pi / 180, 42 * pi / 180
    right = np.array([-sin(azimuth), cos(azimuth), 0])
    up = np.array([-sin(elevation) * cos(azimuth), -sin(elevation) * sin(azimuth), cos(elevation)])
    camera = np.array([cos(elevation) * cos(azimuth), cos(elevation) * sin(azimuth), sin(elevation)])
    projection = np.stack([right, up, camera], axis=1)
    projected = [np.asarray(face) @ projection for face, _ in faces]
    bounds = np.concatenate(projected)
    minx, miny, _ = bounds.min(axis=0)
    maxx, maxy, _ = bounds.max(axis=0)
    width, depth = size
    scale = min((width - 110) / (maxx - minx), (depth - 80) / (maxy - miny))
    ox, oy = width / 2 - (maxx + minx) * scale / 2, depth / 2 + (maxy + miny) * scale / 2
    colors = np.empty((depth, width, 3), dtype=np.uint8)
    colors[:] = rgb(BG)
    zbuffer = np.full((depth, width), -np.inf)
    light = np.array([-.25, .55, 1.0])
    light /= np.linalg.norm(light)
    palette = PALETTES[project.req["style"]]
    floor_pattern = palette.get("floor_pattern", "wood")
    finish_patterns = {}
    interior = project.req.get("interior")
    if interior:
        for element in interior["elements"]:
            if element["kind"] == "floor_finish":
                material = element["material"]
                finish_patterns[rgb(element["color"])] = (
                    "wood" if material in ("oak", "walnut") else "stone")
    for index, ((xyz, color), coords, owner) in enumerate(zip(faces, projected, owners, strict=True)):
        normal = np.cross(np.subtract(xyz[1], xyz[0]), np.subtract(xyz[2], xyz[0]))
        norm = np.linalg.norm(normal)
        if norm <= .001:
            continue
        shade = .74 + .24 * max(0, float(normal @ light / norm))
        is_floor = owner == ("floor", 0)
        pattern = finish_patterns.get(color, floor_pattern)
        color = np.clip(np.array(color) * shade + 9, 0, 255).astype(np.uint8)
        screen = np.column_stack((ox + coords[:, 0] * scale, oy - coords[:, 1] * scale, coords[:, 2]))
        for i in range(1, len(screen) - 1):
            a, b, c = screen[[0, i, i + 1]]
            left = max(0, int(np.floor(min(a[0], b[0], c[0]))))
            right_px = min(width - 1, int(np.ceil(max(a[0], b[0], c[0]))))
            top = max(0, int(np.floor(min(a[1], b[1], c[1]))))
            bottom = min(depth - 1, int(np.ceil(max(a[1], b[1], c[1]))))
            if right_px < left or bottom < top:
                continue
            denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(denominator) < 1e-7:
                continue
            yy, xx = np.ogrid[top:bottom + 1, left:right_px + 1]
            wa = ((b[1] - c[1]) * (xx + .5 - c[0]) + (c[0] - b[0]) * (yy + .5 - c[1])) / denominator
            wb = ((c[1] - a[1]) * (xx + .5 - c[0]) + (a[0] - c[0]) * (yy + .5 - c[1])) / denominator
            wc = 1 - wa - wb
            z = a[2] * wa + b[2] * wb + c[2] * wc
            current = zbuffer[top:bottom + 1, left:right_px + 1]
            visible = (wa >= -1e-7) & (wb >= -1e-7) & (wc >= -1e-7) & (z > current)
            current[visible] = z[visible]
            pixels = colors[top:bottom + 1, left:right_px + 1]
            texture = textures.get(index) if textures else None
            if texture is not None:
                swatch, uv, repeat = texture
                weights = np.stack([wa[visible], wb[visible], wc[visible]], axis=1)
                mapped = weights @ uv[[0, i, i+1]]
                u, v = mapped.T
                u = u % 1 if repeat else np.clip(u, 0, 1)
                v = v % 1 if repeat else np.clip(v, 0, 1)
                tx = np.minimum((u * swatch.shape[1]).astype(int), swatch.shape[1] - 1)
                ty = np.minimum(((1 - v) * swatch.shape[0]).astype(int), swatch.shape[0] - 1)
                pixels[visible] = np.clip(swatch[ty, tx] * shade + 9, 0, 255)
            elif is_floor:
                weights = np.stack([wa[visible], wb[visible], wc[visible]], axis=1)
                world = weights @ np.asarray(xyz)[[0, i, i+1], :2]
                tone = floor_texture(world, pattern)
                pixels[visible] = np.clip(color.astype(int) + tone[:, None], 0, 255)
            else:
                pixels[visible] = color
    silhouette = Image.fromarray(np.where(np.isfinite(zbuffer), 100, 0).astype(np.uint8))
    shadow = Image.new("L", (width, depth), 0)
    shadow.paste(silhouette, (8, 22))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    frame = Image.new("RGB", (width, depth), BG)
    frame.paste("#67786a", (0, 0, width, depth), shadow)
    frame.paste(Image.fromarray(colors), (0, 0), silhouette.point(lambda value: 255 if value else 0))
    return frame


def axon_image(project, placements, label, angle):
    owners = []
    faces = scene(project, placements, angle * pi / 180, owners=owners)
    frame = axon_frame(project, faces, owners, angle)
    board = Image.new("RGB", (frame.width, 975), BG)
    palette = PALETTES[project.req["style"]]
    d = heading(board, project.title, f"{project.req['style']}  /  同一布局的三维体块示意", label,
                palette.get("accent", ACCENT))
    board.paste(frame, (0, 146))
    d.text((57, 932), "家具与平面同坐标 · 近侧剖切 · 墙厚、板厚及未录入的标高为示意",
           font=font(19), fill=MUTED)
    return board


def floor_texture(world, pattern):
    x, y = world.T
    if pattern == "concrete":
        row, column = np.floor(y / 2700), np.floor(x / 2700)
        mottling = 1.3 * np.sin(x / 73 + y / 53) + 1.7 * np.cos(x / 213 - y / 161)
        seam = (y % 2700 < 10) | (x % 2700 < 10)
        return np.rint(mottling + (row * 5 + column * 7) % 5 - 2 - 6 * seam).astype(int)
    if pattern == "stone":
        row, column = np.floor(y / 1100), np.floor(x / 1100)
        seam = (y % 1100 < 8) | (x % 1100 < 8)
        return ((row * 9 + column * 5) % 7 - 3 - seam * 9).astype(int)
    row_width, board_length, stagger = (185, 1800, 600) if pattern == "light_oak" else (260, 1620, 530)
    row = np.floor(y / row_width).astype(int)
    shifted_x = x + stagger * (row % 3)
    column = np.floor(shifted_x / board_length).astype(int)
    tone = ((row * 13 + column * 7) % 9) - 4
    seam = (y % row_width < 6) | (shifted_x % board_length < 7)
    return tone - seam * 9
