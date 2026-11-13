"""Conservative CAD import and raster calibration."""

from collections import Counter, defaultdict
from copy import deepcopy
from math import dist
from pathlib import Path

import ezdxf
import pypdfium2
from PIL import Image, ImageOps
from shapely.geometry import LineString
from shapely.ops import polygonize_full

from model import InputError, as_ring, number, object_fields, parse, points


DEFAULT_LAYERS = {
    "outline": ["OPC_OUTLINE"], "void": ["OPC_VOID"], "column": ["OPC_COLUMN"], "wall": ["OPC_FIXED_WALL"],
    "service": ["OPC_SERVICE"], "existing": ["OPC_FIXED"], "door_swing": ["OPC_DOOR_SWING"],
    "reserved": ["OPC_KEEP_CLEAR"], "no_walk": ["OPC_NO_WALK"], "exit": ["OPC_EXIT"], "window": ["OPC_WINDOW"],
}
UNIT_MM = {1: 25.4, 2: 304.8, 4: 1, 5: 10, 6: 1000}
NAMED_UNITS = {"mm": 1, "cm": 10, "m": 1000, "in": 25.4, "ft": 304.8}


def read_dxf(path):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise InputError("DXF 超过 32 MB。请导出单层、单区域图纸")
    try:
        return ezdxf.readfile(path)
    except (ezdxf.DXFError, UnicodeError) as exc:
        raise InputError(f"DXF 读取失败，请另存标准 DXF。{exc}") from exc


def inspect(path, preview=None, page=1):
    kind = path.suffix.lower()
    if kind == ".dxf":
        doc = read_dxf(path)
        layers = defaultdict(Counter)
        for entity in doc.modelspace():
            layers[entity.dxf.layer][entity.dxftype()] += 1
        return {"type": "dxf", "unit_code": doc.units, "unit_to_mm": UNIT_MM.get(doc.units),
                "layers": {layer: dict(count) for layer, count in sorted(layers.items())}}
    if kind == ".dwg":
        return {"type": "dwg", "status": "requires_conversion", "next": "由 CAD 软件导出 DXF 后导入"}
    if kind == ".pdf":
        pdf = pypdfium2.PdfDocument(path)
        try:
            if not 1 <= page <= len(pdf):
                raise InputError(f"PDF 页码应为 1 至 {len(pdf)}")
            info = {"type": "pdf", "pages": len(pdf), "selected_page": page, "scale_confirmed": False}
            if preview:
                selected = pdf[page - 1]
                try:
                    scale = min(2.5, 3000 / max(selected.get_size()))
                    bitmap = selected.render(scale=scale)
                    try:
                        image = bitmap.to_pil()
                        image.save(preview)
                        info["preview_pixels"] = list(image.size)
                    finally:
                        bitmap.close()
                finally:
                    selected.close()
            return info
        finally:
            pdf.close()
    if kind in (".jpg", ".jpeg", ".png"):
        with Image.open(path) as source:
            if source.width * source.height > 45_000_000:
                raise InputError("图像像素过大，请先裁剪目标区域")
            image = ImageOps.exif_transpose(source)
            if preview:
                image.thumbnail((3000, 3000))
                image.convert("RGB").save(preview)
            return {"type": kind[1:], "pixels": list(image.size), "scale_confirmed": False}
    raise InputError("图纸检查支持 DXF、DWG、PDF、PNG、JPG")


def entity_points(entity, scale):
    kind = entity.dxftype()
    if kind == "LINE":
        vertices, closed = [entity.dxf.start, entity.dxf.end], False
    elif kind == "LWPOLYLINE":
        if any(abs(value) > 1e-12 for (value,) in entity.get_points("b")):
            raise InputError("选定图层含圆弧多段线。请确认近似精度后先将其转为直线段")
        vertices, closed = list(entity.vertices_in_wcs()), entity.closed
    elif kind == "POLYLINE" and entity.is_2d_polyline:
        if any(abs(v.dxf.bulge) > 1e-12 for v in entity.vertices):
            raise InputError("选定图层含带弧度的多段线，请先转换")
        vertices, closed = list(entity.points_in_wcs()), entity.is_closed
    else:
        raise InputError(f"选定图层 {entity.dxf.layer} 含 {kind}。请在 CAD 中展开块或整理实体")
    if len(vertices) < 2 or any(abs(v.z) > .01 for v in vertices):
        raise InputError("选定图层须为 XY 平面上的二维图元")
    return [[round(v.x * scale, 6), round(v.y * scale, 6)] for v in vertices], closed


def import_dxf(path, seed, layers=None, unit=None):
    doc = read_dxf(path)
    scale = NAMED_UNITS.get(unit) if unit else UNIT_MM.get(doc.units)
    if scale is None:
        raise InputError(f"DXF 单位未知或未支持（$INSUNITS={doc.units}）。请用 --unit 指定已核实单位")
    layers = DEFAULT_LAYERS if layers is None else object_fields(layers, " ".join(DEFAULT_LAYERS), "图层映射")
    role_by_layer = {}
    for role, names in layers.items():
        if not isinstance(names, list) or any(not isinstance(s, str) for s in names):
            raise InputError(f"图层映射 {role} 应为图层名列表")
        for name in names:
            if name.casefold() in role_by_layer:
                raise InputError(f"图层 {name} 被赋予多个角色")
            role_by_layer[name.casefold()] = role
    values = defaultdict(list)
    unselected = Counter()
    for entity in doc.modelspace():
        layer = entity.dxf.layer
        role = role_by_layer.get(layer.casefold())
        if role is None:
            unselected[layer] += 1
            continue
        xy, closed = entity_points(entity, scale)
        values[role].append((xy, closed))
    loops = values["outline"]
    if len(loops) == 1 and loops[0][1]:
        outline = loops[0][0]
    else:
        lines = [LineString(xy + ([xy[0]] if closed else [])) for xy, closed in loops]
        polygons, cuts, dangles, invalid = polygonize_full(lines)
        if len(polygons.geoms) != 1 or not all(g.is_empty for g in (cuts, dangles, invalid)):
            raise InputError("轮廓图层须形成唯一闭合净使用轮廓。请检查图层映射、断线和重复线")
        outline = as_ring(polygons.geoms[0])

    geometry = {"unit": "mm", "source": f"DXF {path.name}，输入坐标×{scale:g} 转为 mm",
                "confirmed": False, "outline": outline, "fixed": [], "keep_clear": [], "exits": [], "windows": []}
    if values["void"]:
        geometry["voids"] = []
        for i, (xy, closed) in enumerate(values["void"], 1):
            if not closed:
                raise InputError(f"楼板洞口 {i} 须为闭合多边形")
            geometry["voids"].append({"name": f"楼板洞口 {i}", "polygon": xy})
    for role in ("column", "wall", "service", "existing", "door_swing", "reserved", "no_walk"):
        for i, (xy, closed) in enumerate(values[role], 1):
            if not closed:
                raise InputError(f"{role} 第 {i} 个图元须为闭合多边形")
            key = "keep_clear" if role in ("door_swing", "reserved", "no_walk") else "fixed"
            geometry[key].append({"name": f"{role} {i}", "kind": role, "polygon": xy})
    for role, key in (("exit", "exits"), ("window", "windows")):
        for i, (xy, closed) in enumerate(values[role], 1):
            if closed or len(xy) != 2:
                raise InputError(f"{role} 第 {i} 个开口须使用线段表示两端")
            geometry[key].append({"name": f"{'出口' if role == 'exit' else '窗'} {i}", "segment": xy})
    if seed.get("geometry", {}).get("ceiling_mm") is not None:
        geometry["ceiling_mm"] = seed["geometry"]["ceiling_mm"]
    result = {**seed, "geometry": geometry}
    parse(result, require_confirmed=False)
    return result, {"unmapped_layers": dict(unselected), "unit_to_mm": scale,
                    "next": "核对原图、未映射图层、固定结构、出口和尺度后确认"}


def calibrate(raw, calibration):
    """Convert manually traced pixels on a rectified image to a draft mm project."""
    object_fields(calibration, "a b known_mm rectified second", "标定")
    if calibration.get("rectified") is not True:
        raise InputError("请先使用正投影图，或把照片校正成正投影平面")
    a, b = points([calibration.get("a"), calibration.get("b")], "标定端点", 2, 2)
    known = number(calibration.get("known_mm"), "实测尺寸 mm", 100, 100_000)
    span = dist(a, b)
    if span < 20:
        raise InputError("标定线段少于 20 像素，请选更长的已知尺寸")
    scale = known / span
    if calibration.get("second") is not None:
        second = object_fields(calibration["second"], "a b known_mm", "复核尺寸")
        c, d = points([second.get("a"), second.get("b")], "复核端点", 2, 2)
        other = number(second.get("known_mm"), "复核实测尺寸 mm", 100, 100_000)
        if abs(dist(c, d) * scale / other - 1) > .03:
            raise InputError("两条已知尺寸的比例偏差超过 3%，请检查透视、缩放或端点")
    result = deepcopy(raw)
    g = result.get("geometry", {})
    if g.get("unit") != "px":
        raise InputError("描图文件 geometry.unit 须为 px")

    def convert(seq, key, minimum=3):
        return [[round((x - a[0]) * scale, 4), round((a[1] - y) * scale, 4)]
                for x, y in points(seq, key, minimum)]

    g["outline"] = convert(g.get("outline"), "outline")
    for group, key in (("fixed", "polygon"), ("keep_clear", "polygon"), ("voids", "polygon"),
                       ("exits", "segment"), ("windows", "segment")):
        for row in g.get(group, []):
            row[key] = convert(row.get(key), key, 2 if key == "segment" else 3)
    requirements = result.get("requirements")
    if not isinstance(requirements, dict):
        raise InputError("requirements 应为对象")
    if requirements.get("interior") is not None:
        raise InputError("室内设计元素须在标定后按已确认的毫米图录入")
    zones = requirements.get("zones", [])
    if not isinstance(zones, list):
        raise InputError("zones 应为列表")
    for zone in zones:
        object_fields(zone, "use polygon", "指定使用区")
        zone["polygon"] = convert(zone.get("polygon"), "polygon")
    for group in requirements.get("work_groups", []):
        object_fields(group, "name desks polygon", "团队分区")
        group["polygon"] = convert(group.get("polygon"), "团队分区")
    for route in requirements.get("clear_routes", []):
        object_fields(route, "name points width_mm", "指定通道")
        route["points"] = convert(route.get("points"), "指定通道", 2)
    for approach in requirements.get("required_approaches", []):
        object_fields(approach, "name point entry", "门前检测点")
        approach["point"], = convert([approach.get("point")], "门前检测点", 1)
    if any("placement" in row for row in requirements.get("furniture", []) if isinstance(row, dict)):
        raise InputError("家具固定位置请在图纸标定后按毫米坐标录入")
    g["unit"], g["confirmed"] = "mm", False
    g["source"] = f"{g.get('source', '平面图描图')}，以 {known:g} mm 尺寸标定，{scale:.6g} mm/px"
    parse(result, require_confirmed=False)
    return result
