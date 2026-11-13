"""Dimensioned outdoor elements, their clearance and local views."""

import csv
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.affinity import rotate, scale
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, unary_union

from model import InputError, as_ring, color_hex, label, number, object_fields, points, polygon
from visual import axon_frame, font, rgb, shorten, surface_triangles


KINDS = {"bench": "座椅", "tree": "乔木", "planter": "种植池", "lamp": "景观灯",
         "bin": "垃圾桶", "sculpture": "艺术装置", "shelter": "遮棚", "sign": "导视牌",
         "equipment": "定制设施"}


def validate_features(raw, data):
    rows = raw.get("features", [])
    if not isinstance(rows, list) or len(rows) > 120:
        raise InputError("户外设施应为不超过 120 项的列表")
    names = {x["name"] for key in ("buildings", "paths", "entries", "zones") for x in data[key]}
    features = []
    for index, row in enumerate(rows, 1):
        object_fields(row, "name kind polygon height_mm color note access_point components facing", "户外设施")
        name = label(row.get("name"), "户外设施名称")
        if name in names:
            raise InputError(f"名称重复：{name}")
        names.add(name)
        kind = row.get("kind")
        if kind not in KINDS:
            raise InputError("户外设施 kind 须为 " + "、".join(KINDS))
        shape = polygon(row.get("polygon"), name)
        if not data["site"].covers(shape):
            raise InputError(f"户外设施 {name} 超出地块边界")
        if any(shape.intersection(item["shape"]).area > 1 for item in data["buildings"]):
            raise InputError(f"户外设施 {name} 与建筑重叠")
        if any(shape.intersection(item["shape"]).area > 1 for item in data["paths"]):
            raise InputError(f"户外设施 {name} 侵占道路范围")
        if any(shape.intersection(item["shape"]).area > 1 for item in features):
            raise InputError(f"户外设施 {name} 与其他设施重叠")
        height = number(row.get("height_mm"), f"{name}高度", 50, 30000)
        color = color_hex(row.get("color"), f"{name}颜色")
        note = label(row["note"], f"{name}说明") if row.get("note") else ""
        facing = row.get("facing", "north")
        if facing not in ("north", "south", "east", "west"):
            raise InputError(f"户外设施 {name} 的 facing 应为 north、south、east 或 west")
        components = row.get("components", [])
        if not isinstance(components, list) or len(components) > 32:
            raise InputError(f"户外设施 {name} 的 components 须为不超过 32 项的列表")
        parsed_components = []
        for component in components:
            object_fields(component, "name polygon base_mm top_mm color", f"{name}部件")
            part_name = label(component.get("name"), f"{name}部件名称")
            part = polygon(component.get("polygon"), part_name)
            if not shape.covers(part):
                raise InputError(f"户外设施 {name} 的部件 {part_name} 超出外廓")
            base = number(component.get("base_mm"), f"{part_name}底标高", 0, height - 1)
            top = number(component.get("top_mm"), f"{part_name}顶标高", base + 1, height)
            parsed_components.append({"name": part_name, "shape": part, "base": base, "top": top,
                                      "color": color_hex(component.get("color"), f"{part_name}颜色")})
        access = None
        if row.get("access_point") is not None:
            access = Point(points([row["access_point"]], f"{name}操作点", 1, 1)[0])
            if not data["site"].covers(access) or shape.contains(access) or shape.distance(access) > 2000:
                raise InputError(f"户外设施 {name} 操作点须在场地内、距离设施不超过 2 米")
        features.append({"id": f"E{index:03d}", "name": name, "kind": kind, "shape": shape,
                         "height": height, "color": color, "note": note, "access": access,
                         "components": parsed_components, "facing": facing})
    paths = unary_union([data["paths"][i]["shape"] for i in data["connected"]])
    blockers = [item["shape"] for item in data["buildings"] + features]
    for feature in features:
        point = feature["access"]
        feature["access_status"] = "待确认"
        if point is not None:
            if paths.is_empty:
                feature["access_status"] = "未连通"
                continue
            distance = paths.distance(point)
            endpoint = nearest_points(point, paths)[1]
            connector = LineString([point, endpoint])
            blocked = any(connector.intersection(shape.buffer(-1)).length > 1 for shape in blockers)
            feature["access_status"] = "平面直线可达" if distance <= 3000 and not blocked else "待调整"
    return features


def feature_faces(feature):
    shape, height, kind = feature["shape"], feature["height"], feature["kind"]
    color = feature["color"]
    faces = []

    def solid(polygon, bottom, top, paint):
        for triangle in surface_triangles(polygon):
            faces.append(([(x, y, top) for x, y in triangle], rgb(paint)))
        ring = list(polygon.exterior.coords)
        for a, b in zip(ring, ring[1:]):
            faces.append(([(a[0], a[1], bottom), (b[0], b[1], bottom),
                           (b[0], b[1], top), (a[0], a[1], top)], rgb(paint)))

    if feature["components"]:
        for part in feature["components"]:
            solid(part["shape"], part["base"], part["top"], part["color"])
    elif kind == "tree":
        circle = shape.centroid.buffer(1, quad_segs=8)
        x0, y0, x1, y1 = shape.bounds
        canopy = scale(circle, (x1-x0)/2, (y1-y0)/2).intersection(shape)
        solid(scale(canopy, .12, .12), 0, height * .68, "#75634e")
        solid(scale(canopy, .62, .62), height * .55, height * .85, color)
        solid(canopy, height * .70, height, color)
    elif kind == "lamp":
        solid(scale(shape, .20, .20), 0, height * .9, color)
        solid(shape, height * .90, height, "#ded9b8")
    elif kind == "bench":
        solid(scale(shape, .83, .60), 0, height * .48, "#6f7568")
        solid(shape, height * .48, height * .56, color)
        x0, y0, x1, y1 = shape.bounds
        if feature["facing"] == "north":
            back = box(x0, y0, x1, y0 + (y1-y0)*.14)
        elif feature["facing"] == "south":
            back = box(x0, y1-(y1-y0)*.14, x1, y1)
        elif feature["facing"] == "east":
            back = box(x0, y0, x0+(x1-x0)*.14, y1)
        else:
            back = box(x1-(x1-x0)*.14, y0, x1, y1)
        back = back.intersection(shape)
        if isinstance(back, Polygon) and not back.is_empty:
            solid(back, height * .56, height, color)
    elif kind == "shelter":
        solid(scale(shape, .10, .10, origin=shape.exterior.coords[0]), 0, height, "#68756d")
        solid(scale(shape, .10, .10, origin=shape.exterior.coords[2]), 0, height, "#68756d")
        solid(shape, height * .91, height, color)
    elif kind == "planter":
        solid(shape, 0, height * .72, color)
        solid(scale(shape, .80, .80), height * .72, height, "#78936d")
    elif kind == "sculpture":
        solid(scale(shape, .82, .82), 0, height * .20, "#666b62")
        tilted = rotate(scale(shape, .48, .48), 24).intersection(shape)
        solid(tilted, height * .20, height, color)
    elif kind == "bin":
        solid(shape, 0, height * .90, color)
        solid(scale(shape, .90, .90), height * .90, height * .98, "#48574d")
        solid(scale(shape, .32, .30), height * .98, height, "#202c28")
    else:
        solid(shape, 0, height, color)
    return faces


def export_details(out, data, features, model):
    root = Path(out)
    with (root / "户外设施定位.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["编号", "名称", "类型", "外接宽 mm", "外接深 mm", "高 mm", "轮廓点（图纸 X Y mm）",
                         "操作点（图纸 X Y mm）", "接近检查", "说明"])
        for item in features:
            x0, y0, x1, y1 = item["shape"].bounds
            coordinates = " / ".join(f"({x:.3f}, {y:.3f})" for x, y in as_ring(item["shape"]))
            name = item["name"]
            if name.lstrip().startswith(("=", "+", "-", "@")):
                name = "'" + name
            note = item["note"]
            if note.lstrip().startswith(("=", "+", "-", "@")):
                note = "'" + note
            writer.writerow([item["id"], name, KINDS[item["kind"]], round(x1-x0, 3), round(y1-y0, 3),
                             item["height"], coordinates,
                             f"({item['access'].x:.3f}, {item['access'].y:.3f})" if item["access"] else "",
                             item["access_status"], note])
    for page, start in enumerate(range(0, len(features), 6), 1):
        batch = features[start:start+6]
        image = Image.new("RGB", (1500, 145 + ((len(batch) + 2) // 3) * 360), "#f2f1ea")
        draw = ImageDraw.Draw(image)
        draw.text((37, 28), f"户外设施 · 逐件查看 {page}", font=font(40, True), fill="#283b32")
        draw.text((39, 91), "外廓和高度来自同一园区方案；完整定位见设施表。", font=font(22), fill="#617267")
        for i, item in enumerate(batch):
            left, top = 38 + i % 3 * 480, 135 + i // 3 * 360
            draw.rounded_rectangle((left, top, left+460, top+343), 14, fill="#fffefa", outline="#d8dfd5", width=2)
            caption = shorten(draw, f"{item['id']}  {item['name']}", font(24, True), 420)
            draw.text((left+17, top+12), caption, font=font(24, True), fill="#283b32")
            faces = feature_faces(item)
            frame = axon_frame(model, faces, [None] * len(faces), 45, size=(420, 208))
            image.paste(frame, (left+20, top+49))
            x0, y0, x1, y1 = item["shape"].bounds
            draw.text((left+19, top+265), f"{x1-x0:g} × {y1-y0:g} × {item['height']:g} mm",
                      font=font(18), fill="#283b32")
            draw.text((left+19, top+296), shorten(draw, item["note"] or item["access_status"], font(18), 420),
                      font=font(18), fill="#617267")
        image.save(root / f"户外设施局部-{page:02d}.png")
