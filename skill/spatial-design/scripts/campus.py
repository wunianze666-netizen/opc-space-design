"""Campus masterplan review from explicitly supplied site geometry."""

from collections import deque
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace

import ezdxf
from PIL import Image, ImageDraw
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from model import InputError, as_ring, color_hex, digest, flag, label, number, object_fields, points, polygon, write_json
from visual import PALETTES, axon_frame, font, rgb, shorten, surface_triangles


def validate(raw):
    object_fields(raw, "title source confirmed boundary zones buildings paths entries features surfaces", "园区项目")
    title = label(raw.get("title"), "园区名称")
    source = label(raw.get("source"), "底图来源")
    if not flag(raw.get("confirmed"), "园区底图确认"):
        raise InputError("请先核对园区边界、建筑轮廓、出入口和尺寸，再确认底图")
    site = polygon(raw.get("boundary"), "园区边界")
    identifiers = set()

    def records(key, allowed):
        result = raw.get(key)
        if not isinstance(result, list) or len(result) > 80 or key in ("buildings", "paths", "entries") and not result:
            raise InputError(f"{key} 应为不超过 80 项的列表，建筑、道路和入口须提供")
        for row in result:
            object_fields(row, allowed, key)
            name = label(row.get("name"), "名称")
            if name in identifiers:
                raise InputError(f"名称重复：{name}")
            identifiers.add(name)
        return result

    zones = []
    for row in records("zones", "name polygon use"):
        shape = polygon(row.get("polygon"), row["name"])
        use = label(row.get("use"), "分区用途")
        if not site.covers(shape):
            raise InputError(f"分区 {row['name']} 超出园区边界")
        if any(shape.intersection(old["shape"]).area > 1 for old in zones):
            raise InputError(f"分区 {row['name']} 与其他分区重叠")
        zones.append({"name": row["name"], "use": use, "shape": shape})
    buildings = []
    for row in records("buildings", "name polygon height_mm entrance state facade"):
        shape = polygon(row.get("polygon"), row["name"])
        if not site.covers(shape):
            raise InputError(f"建筑 {row['name']} 超出园区边界")
        if any(shape.intersection(old["shape"]).area > 1 for old in buildings):
            raise InputError(f"建筑 {row['name']} 与其他建筑重叠")
        height = number(row.get("height_mm"), "建筑高度 mm", 1000, 200000)
        entrance = Point(points([row.get("entrance")], "建筑入口", 1, 1)[0])
        if shape.boundary.distance(entrance) > 1:
            raise InputError(f"建筑入口 {row['name']} 须位于建筑轮廓上")
        state = row.get("state")
        if state not in ("existing", "proposed"):
            raise InputError(f"建筑 {row['name']} 的 state 应为 existing 或 proposed")
        facade = row.get("facade")
        if facade is not None:
            facade = object_fields(facade, "glass_color floors sill_mm head_mm bay_mm", f"{row['name']}立面")
            floors = number(facade.get("floors"), "立面层数", 1, 20, integer=True)
            floor_height = height / floors
            sill = number(facade.get("sill_mm"), "窗台距楼面 mm", 0, floor_height - 100)
            head = number(facade.get("head_mm"), "窗顶距楼面 mm", sill + 100, floor_height)
            facade = {"glass_color": color_hex(facade.get("glass_color"), "立面玻璃色"),
                      "floors": floors, "sill_mm": sill, "head_mm": head,
                      "bay_mm": number(facade.get("bay_mm"), "立面分格宽度 mm", 800, 10000)}
        buildings.append({"name": row["name"], "shape": shape, "height": height, "entrance": entrance,
                          "state": state, "facade": facade})
    paths = []
    for row in records("paths", "name points width_mm"):
        line = LineString(points(row.get("points"), row["name"], 2, 200))
        width = number(row.get("width_mm"), "道路宽度 mm", 300, 30000)
        if not line.is_simple or line.length < 1 or not site.covers(line):
            raise InputError(f"道路 {row['name']} 的中心线须位于园区内且不可自交")
        paved = line.buffer(width / 2, cap_style="flat", join_style="mitre")
        if not site.buffer(1).covers(paved):
            raise InputError(f"道路 {row['name']} 的宽度范围超出园区边界")
        if any(paved.intersection(b["shape"]).area > 1 for b in buildings):
            raise InputError(f"道路 {row['name']} 的宽度范围与建筑重叠")
        paths.append({"name": row["name"], "line": line, "width": width, "shape": paved})
    entries = []
    for row in records("entries", "name point"):
        entry = Point(points([row.get("point")], "园区入口", 1, 1)[0])
        if site.boundary.distance(entry) > 1:
            raise InputError(f"园区入口 {row['name']} 须位于园区边界")
        entries.append({"name": row["name"], "point": entry})
    connected = set(i for i, route in enumerate(paths) if any(route["shape"].distance(e["point"]) <= 1 for e in entries))
    queue = deque(connected)
    while queue:
        i = queue.popleft()
        for j, route in enumerate(paths):
            if j not in connected and route["shape"].distance(paths[i]["shape"]) <= 1:
                connected.add(j)
                queue.append(j)
    coverage = unary_union([paths[i]["shape"] for i in connected])
    checks = [{"building": b["name"], "reachable": not coverage.is_empty and coverage.distance(b["entrance"]) <= 1}
              for b in buildings]
    surfaces = []
    for row in records("surfaces", "name polygon color note") if "surfaces" in raw else []:
        shape = polygon(row.get("polygon"), row["name"])
        if not site.covers(shape) or any(shape.intersection(b["shape"]).area > 1 for b in buildings):
            raise InputError(f"景观地面 {row['name']} 超出场地或与建筑重叠")
        if any(shape.intersection(other["shape"]).area > 1 for other in surfaces):
            raise InputError(f"景观地面 {row['name']} 与其他景观地面重叠")
        surfaces.append({"name": row["name"], "shape": shape,
                         "color": color_hex(row.get("color"), "景观地面颜色"),
                         "note": label(row["note"], "景观地面说明") if row.get("note") else ""})
    data = {"title": title, "source": source, "site": site, "zones": zones, "buildings": buildings,
            "surfaces": surfaces,
            "paths": paths, "entries": entries, "checks": checks, "connected": connected}
    from campus_features import validate_features
    data["features"] = validate_features(raw, data)
    return data


def generate(raw, out, *, quality="quick", blender=None):
    data = validate(raw)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    site, zones, buildings, paths = (data[key] for key in ("site", "zones", "buildings", "paths"))
    x0, y0, x1, y1 = site.bounds
    width, height = 1800, 1240
    image = Image.new("RGB", (width, height), "#f2f1ea")
    draw = ImageDraw.Draw(image)
    draw.text((52, 38), data["title"], font=font(52, True), fill="#283b32")
    draw.text((54, 113), "园区总平面 · 尺寸单位为米", font=font(25), fill="#617267")
    scale = min(1190 / (x1 - x0), 960 / (y1 - y0))
    ox, oy = 70 + (1190 - (x1 - x0) * scale) / 2, 1160 - (960 - (y1 - y0) * scale) / 2
    def xy(point):
        x, y = point
        return ox + (x - x0) * scale, oy - (y - y0) * scale
    def draw_shape(shape, fill, edge):
        shapes = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
        for part in shapes:
            if isinstance(part, Polygon) and not part.is_empty:
                draw.polygon([xy(p) for p in part.exterior.coords], fill=fill, outline=edge, width=3)
    draw_shape(site, "#fdfcf7", "#4c6455")
    for i, zone in enumerate(zones):
        draw_shape(zone["shape"], ("#dce9d7", "#ece1c9", "#dbe5e7", "#eee0d5")[i % 4], "#b9cbb9")
        p = zone["shape"].representative_point()
        label_position = xy(p.coords[0])
        draw.text((label_position[0] + 15, label_position[1] + 18), zone["use"], font=font(21), fill="#678071")
    for surface in data["surfaces"]:
        draw_shape(surface["shape"], surface["color"], surface["color"])
    for path in paths:
        draw_shape(path["shape"], "#c7c3b4", "#b8b4a7")
    for i, building in enumerate(buildings):
        draw_shape(building["shape"], "#a9bbb2" if building["state"] == "existing" else "#bd936d", "#435b4c")
        p = building["shape"].representative_point()
        draw.text(xy(p.coords[0]), str(i + 1), font=font(30, True), fill="#ffffff", anchor="mm")
        ex, ey = xy(building["entrance"].coords[0])
        draw.ellipse((ex-7, ey-7, ex+7, ey+7), fill="#356e5b", outline="white", width=2)
    for entry in data["entries"]:
        ex, ey = xy(entry["point"].coords[0])
        draw.ellipse((ex-9, ey-9, ex+9, ey+9), fill="#b85d43", outline="white", width=2)
    for feature in data["features"]:
        draw_shape(feature["shape"], feature["color"], "#435b4c")
        p = feature["shape"].representative_point()
        ex, ey = xy(p.coords[0])
        draw.ellipse((ex-4, ey-4, ex+4, ey+4), fill=feature["color"], outline="#ffffff")
        draw.text((ex+5, ey-15), feature["id"], font=font(14, True), fill="#344a3e",
                  stroke_width=2, stroke_fill="#fffefa")
    draw.rounded_rectangle((1300, 182, 1750, 1162), radius=18, fill="#fffefa", outline="#d8dfd5", width=2)
    draw.text((1327, 207), "建筑与入口", font=font(30, True), fill="#283b32")
    for i, (building, check) in enumerate(list(zip(buildings, data["checks"], strict=True))[:9]):
        y = 270 + i * 80
        draw.text((1330, y), shorten(draw, f"{i+1}  {building['name']}", font(24, True), 390), font=font(24, True), fill="#283b32")
        line = f"高度 {building['height']/1000:g} m  ·  {'道路相接' if check['reachable'] else '入口未接通'}"
        draw.text((1332, y+34), line, font=font(19), fill="#617267")
    if len(buildings) > 9:
        draw.text((1332, 1000), f"其余 {len(buildings) - 9} 项见园区核对文件", font=font(19), fill="#617267")
    draw.text((1327, 1025), "绿点  建筑入口", font=font(21), fill="#356e5b")
    draw.text((1327, 1060), "红点  园区入口", font=font(21), fill="#b85d43")
    if data["features"]:
        draw.text((1327, 1095), "E 编号  设施定位表", font=font(19), fill="#617267")
    draw.text((52, 1200), "按输入的二维几何检查道路和入口连接。建筑高度及现场条件依输入资料。", font=font(18), fill="#617267")
    image.save(out / "园区总平面.png")
    faces, owners = [], []
    def horizontal(shape, z, color, owner=None):
        for triangle in surface_triangles(shape):
            faces.append(([(x, y, z) for x, y in triangle], rgb(color)))
            owners.append(owner)
    horizontal(site, 0, "#d6e0d0", ("site", "边界"))
    for surface in data["surfaces"]:
        horizontal(surface["shape"], 3, surface["color"], ("surface", surface["name"]))
    paved = unary_union([path["shape"] for path in paths])
    shapes = list(paved.geoms) if hasattr(paved, "geoms") else [paved]
    for part in shapes:
        if isinstance(part, Polygon) and part.area > 1:
            horizontal(part, 10, "#bebaae", ("paths", "道路"))
    for building in buildings:
        shape, top = building["shape"], building["height"]
        paint = "#95b0a4" if building["state"] == "existing" else "#c69470"
        horizontal(shape, top, paint, ("building", building["name"]))
        ring = list(shape.exterior.coords)
        for a, b in zip(ring, ring[1:]):
            faces.append(([(a[0], a[1], 0), (b[0], b[1], 0), (b[0], b[1], top),
                           (a[0], a[1], top)], rgb(paint)))
            owners.append(("building", building["name"]))
        facade = building["facade"]
        if facade:
            ring = list(orient(shape, sign=1).exterior.coords)
            for a, b in zip(ring, ring[1:]):
                edge = LineString([a, b])
                dx, dy = (b[0]-a[0])/edge.length, (b[1]-a[1])/edge.length
                nx, ny = dy*12, -dx*12
                count = max(1, int(edge.length // facade["bay_mm"]))
                pitch = edge.length/count
                for i in range(count):
                    start, end = (i+.12)*pitch, (i+.88)*pitch
                    if edge.distance(building["entrance"]) < 2 and start-800 <= edge.project(building["entrance"]) <= end+800:
                        continue
                    p = (a[0]+dx*start+nx, a[1]+dy*start+ny)
                    q = (a[0]+dx*end+nx, a[1]+dy*end+ny)
                    for floor in range(facade["floors"]):
                        base = floor*top/facade["floors"]
                        low, high = base+facade["sill_mm"], base+facade["head_mm"]
                        faces.append(([(p[0],p[1],low),(q[0],q[1],low),
                                       (q[0],q[1],high),(p[0],p[1],high)], rgb(facade["glass_color"])))
                        owners.append(("facade", building["name"]))
    from campus_features import export_details, feature_faces
    for feature in data["features"]:
        new_faces = feature_faces(feature)
        faces.extend(new_faces)
        owners.extend([("feature", feature["id"])] * len(new_faces))
    scene_data = {"units": "mm", "input_sha256": digest(raw),
                  "faces": [{"vertices": vertices, "rgb": color, "owner": owner}
                            for (vertices, color), owner in zip(faces, owners, strict=True)]}
    scene_digest = digest(scene_data)
    write_json(out / "园区三维几何.json", scene_data)
    model = SimpleNamespace(req={"style": "绿化", "interior": None})
    axon_frame(model, faces, owners, 225, size=(1600, 1000)).save(out / "园区体量三维.png")
    render = {"status": "not_requested", "engine": "local"}
    if quality == "studio":
        from studio import blender_executable
        executable = blender_executable(blender)
        if executable is None:
            render = {"status": "fallback", "reason": "未找到 Blender", "engine": "local"}
        else:
            try:
                with tempfile.TemporaryDirectory(dir=out, prefix=".render-") as temporary:
                    work = Path(temporary)
                    spec = {"settings": {"resolution_px": [1600, 1040], "samples": 16,
                                         "elevation_degrees": 39, "azimuth_degrees": 225,
                                         "lighting_factor": .36, "exposure_stops": -.35},
                            "palette": PALETTES["绿化"], "origin_mm": [(x0+x1)/2, (y0+y1)/2],
                            "options": [{"id": "A", "faces": faces}]}
                    source = work / "scene.json"
                    source.write_text(json.dumps(spec, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                    command = [executable, "--background", "--factory-startup", "-noaudio",
                               "--python-exit-code", "3", "--python",
                               str(Path(__file__).with_name("studio_blender.py")), "--", str(source), str(work)]
                    environment = dict(os.environ, TEMP=str(work), TMP=str(work), TMPDIR=str(work))
                    process = subprocess.run(command, cwd=work, env=environment, capture_output=True,
                                             text=True, encoding="utf-8", errors="replace", timeout=720,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if process.returncode:
                        raise ValueError("Blender 渲染失败")
                    with Image.open(work / "A.png") as picture:
                        if picture.size != (1600, 1040) or picture.format != "PNG":
                            raise ValueError("渲染图格式或尺寸不符合预期")
                        picture.verify()
                    report = json.loads((work / "report.json").read_text(encoding="utf-8"))
                    (work / "A.png").replace(out / "园区同坐标精渲染.png")
                    render = {"status": "rendered", "engine": "Blender Cycles",
                              "version": report["blender_version"], "file": "园区同坐标精渲染.png"}
            except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
                render = {"status": "fallback", "reason": str(error)[:200], "engine": "local"}
    if data["features"]:
        export_details(out, data, data["features"], model)
    doc = ezdxf.new("R2010")
    doc.units = ezdxf.units.MM
    model = doc.modelspace()
    model.add_lwpolyline(as_ring(site), close=True, dxfattribs={"layer": "SITE"})
    for zone in zones:
        model.add_lwpolyline(as_ring(zone["shape"]), close=True, dxfattribs={"layer": "ZONES"})
    for surface in data["surfaces"]:
        model.add_lwpolyline(as_ring(surface["shape"]), close=True, dxfattribs={"layer": "LANDSCAPE"})
    for b in buildings:
        model.add_lwpolyline(as_ring(b["shape"]), close=True, dxfattribs={"layer": "BUILDINGS"})
        model.add_point(b["entrance"].coords[0], dxfattribs={"layer": "BUILDING_ENTRIES"})
    for path in paths:
        model.add_lwpolyline(path["line"].coords, dxfattribs={"layer": "PATH_CENTERS"})
        model.add_lwpolyline(as_ring(path["shape"]), close=True, dxfattribs={"layer": "PATH_AREAS"})
    for entry in data["entries"]:
        model.add_point(entry["point"].coords[0], dxfattribs={"layer": "SITE_ENTRIES"})
    for feature in data["features"]:
        model.add_lwpolyline(as_ring(feature["shape"]), close=True, dxfattribs={"layer": "FEATURES"})
        model.add_text(feature["id"], height=800, dxfattribs={"layer": "FEATURE_LABELS"}).set_placement(
            feature["shape"].representative_point().coords[0])
        for part in feature["components"]:
            model.add_lwpolyline(as_ring(part["shape"]), close=True, dxfattribs={"layer": "FEATURE_PARTS"})
        if feature["access"] is not None:
            model.add_point(feature["access"].coords[0], dxfattribs={"layer": "FEATURE_ACCESS"})
    doc.saveas(out / "园区总平面.dxf")
    status = "ready" if all(row["reachable"] for row in data["checks"]) and all(
        item["access_status"] != "待调整" for item in data["features"]) else "needs_revision"
    result = {"status": status, "title": data["title"], "area_m2": round(site.area / 1e6, 2),
              "buildings": data["checks"], "path_widths_mm": {p["name"]: p["width"] for p in paths},
              "surfaces": [{"name": item["name"], "polygon_mm": as_ring(item["shape"]), "note": item["note"]}
                           for item in data["surfaces"]],
              "features": [{"id": item["id"], "name": item["name"], "kind": item["kind"],
                            "polygon_mm": as_ring(item["shape"]), "height_mm": item["height"], "note": item["note"],
                            "access_point_mm": list(item["access"].coords[0]) if item["access"] else None,
                            "facing": item["facing"],
                            "components": [{"name": part["name"], "polygon_mm": as_ring(part["shape"]),
                                            "base_mm": part["base"], "top_mm": part["top"], "color": part["color"]}
                                           for part in item["components"]],
                            "access": item["access_status"]} for item in data["features"]],
              "rendering": render,
              "scene_sha256": scene_digest,
              "source": data["source"], "limits": "三维按建筑外轮廓与输入高度形成体量。仅检查道路二维几何连通；竖向、消防、车行和无障碍待专业复核。"}
    write_json(out / "园区核对.json", result)
    return {"out": str(out), **result}
