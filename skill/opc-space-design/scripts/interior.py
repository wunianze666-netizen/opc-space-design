"""Measured, proposed interior finishes and fittings. Existing geometry stays separate."""

from math import dist
from re import fullmatch

from shapely.geometry import LineString, Point, Polygon

from model import InputError, color_hex, label, number, object_fields, points, polygon


MATERIALS = {
    "oak": "#a8794e", "walnut": "#654032", "sage": "#567864", "cobalt": "#294b7b",
    "graphite": "#354449", "clay": "#a85f44", "ivory": "#e4ded0", "terrazzo": "#c1b4a0",
    "warm_light": "#ffd59c", "cork": "#b4865e",
}
WALL_KINDS = {"slats", "acoustic", "art", "display_wall", "notice_board"}
CEILING_KINDS = {"ceiling_light", "ceiling_baffle"}
KINDS = WALL_KINDS | CEILING_KINDS | {"floor_finish"}


def design_layer(kind):
    suffix = "WALL" if kind in WALL_KINDS else "CEILING" if kind in CEILING_KINDS else "FLOOR"
    return f"OPC_INTERIOR_{suffix}"


def mounted_footprint(segment, depth, outline, fixed, openings, name):
    line = LineString(segment)
    if line.length < 200:
        raise InputError(f"{name}的附墙线段应至少 200 mm")
    supports = [outline.exterior, *(s.exterior for _, k, s in fixed if k == "wall")]
    supported = any(LineString([a, b]).buffer(.5).covers(line)
                    for ring in supports for a, b in zip(ring.coords, list(ring.coords)[1:]))
    if not supported:
        raise InputError(f"{name}须挂在已确认的一段实体墙面上")
    conflicts = [opening.name for opening in openings if opening.line.intersection(line.buffer(.5)).length > 1]
    if conflicts:
        raise InputError(f"{name}跨过已确认的开口 {'、'.join(conflicts)}")
    (x0, y0), (x1, y1) = segment
    normal = (-(y1 - y0) / line.length * depth, (x1 - x0) / line.length * depth)
    for sign in (1, -1):
        dx, dy = (value * sign for value in normal)
        shape = Polygon([(x0, y0), (x1, y1), (x1 + dx, y1 + dy), (x0 + dx, y0 + dy)])
        if outline.buffer(.5).covers(shape) and not any(s.intersection(shape).area > .01 for _, _, s in fixed):
            return shape
    raise InputError(f"{name}从墙面突出后与边界、洞口或固定结构冲突")


def parse_interior(raw, outline, fixed, clear, openings, ceiling):
    if raw is None:
        return None
    object_fields(raw, "concept description elements clearance_mm view materials", "室内设计")
    concept = label(raw.get("concept"), "室内设计名称")
    description = label(raw.get("description"), "设计说明")
    clearance = number(raw.get("clearance_mm", 2200), "本方案净空目标 mm", 1800, 4000)
    custom = raw.get("materials", {})
    if not isinstance(custom, dict) or len(custom) > 24:
        raise InputError("自定义材质须为最多 24 项的对象")
    colors = dict(MATERIALS)
    material_names = {}
    for key, item in custom.items():
        if not isinstance(key, str) or not fullmatch(r"[a-z][a-z0-9_]{0,23}", key) or key in MATERIALS:
            raise InputError(f"自定义材质标识 {key} 无效或与预设重名")
        object_fields(item, "name color", "自定义材质")
        colors[key] = color_hex(item.get("color"), f"{key}的颜色")
        material_names[key] = label(item.get("name"), f"{key}的材质名称")
    records = raw.get("elements")
    if not isinstance(records, list) or not 1 <= len(records) <= 80:
        raise InputError("室内设计须提供 1 至 80 项可定位的元素")
    elements, names = [], set()
    for row in records:
        if not isinstance(row, dict):
            raise InputError("室内设计元素应为对象")
        kind = row.get("kind")
        if not isinstance(kind, str) or kind not in KINDS:
            raise InputError(f"室内设计 kind 应为 {' / '.join(sorted(KINDS))}")
        fields = "name kind material "
        fields += "segment depth_mm bottom_mm top_mm" if kind in WALL_KINDS else "polygon"
        if kind in CEILING_KINDS:
            fields += " bottom_mm top_mm"
        object_fields(row, fields, "室内设计元素")
        name = label(row.get("name"), "室内设计元素名称")
        if name in names:
            raise InputError(f"室内设计元素名称重复：{name}")
        names.add(name)
        material = row.get("material")
        if not isinstance(material, str) or material not in colors:
            raise InputError(f"{name}的 material 须引用预设或已声明的自定义材质")
        segment = None
        bottom = top = 0
        if kind != "floor_finish":
            if ceiling is None:
                raise InputError(f"{name}需要已确认的净高，以核对安装标高")
            bottom = number(row.get("bottom_mm"), f"{name}底标高 mm", 0, ceiling)
            top = number(row.get("top_mm"), f"{name}顶标高 mm", bottom + 1, ceiling)
            if kind in CEILING_KINDS and bottom < clearance:
                raise InputError(f"{name}低于本方案 {clearance:g} mm 净空目标")
        if kind in WALL_KINDS:
            segment = points(row.get("segment"), name, 2, 2)
            depth = number(row.get("depth_mm"), f"{name}从墙面突出 mm", 8, 450)
            shape = mounted_footprint(segment, depth, outline, fixed, openings, name)
            if bottom < clearance and any(s.intersection(shape).area > .01 for _, _, s in clear):
                raise InputError(f"{name}占用门扇、禁占区或禁行区")
        else:
            shape = polygon(row.get("polygon"), name)
            if not outline.covers(shape):
                raise InputError(f"{name}越出可用楼板或进入洞口")
            if kind in CEILING_KINDS and any(s.intersection(shape).area > .01 for _, _, s in fixed):
                raise InputError(f"{name}与固定结构投影冲突")
        for other in elements:
            same_floor = kind == other["kind"] == "floor_finish"
            overlap_z = min(top, other["top_mm"]) > max(bottom, other["bottom_mm"])
            if (same_floor or overlap_z) and shape.intersection(other["footprint"]).area > .01:
                raise InputError(f"{name}与{other['name']}重叠")
        elements.append({"name": name, "kind": kind, "material": material, "color": colors[material],
                         "material_name": material_names.get(material), "footprint": shape,
                         "bottom_mm": bottom, "top_mm": top, "segment": segment})
    view = raw.get("view")
    if view is not None:
        object_fields(view, "eye_mm target_mm", "室内人视机位")
        if ceiling is None:
            raise InputError("室内人视机位需要已确认的净高")
        parsed_view = {}
        for key in ("eye_mm", "target_mm"):
            xyz = view.get(key)
            if not isinstance(xyz, list) or len(xyz) != 3:
                raise InputError(f"{key} 应包含三个毫米坐标")
            x, y = (number(v, key, -1e8, 1e8) for v in xyz[:2])
            z = number(xyz[2], f"{key} 标高", 50, ceiling - 20)
            if not outline.covers(Point(x, y)) or (key == "eye_mm" and not outline.contains(Point(x, y))):
                raise InputError(f"{key} 位于边界之外或洞口内")
            parsed_view[key] = [x, y, z]
        if dist(parsed_view["eye_mm"], parsed_view["target_mm"]) < 500:
            raise InputError("人视机位与目标应相距至少 500 mm")
        eye = Point(parsed_view["eye_mm"][:2])
        if any(shape.buffer(70).covers(eye) for _, _, shape in fixed):
            raise InputError("人视机位落在固定结构内")
        view = parsed_view
    return {"concept": concept, "description": description, "elements": elements, "materials": colors,
            "clearance_mm": clearance, "view": view}


def walking_fittings(project):
    design = project.req["interior"]
    if not design:
        return []
    return [(element["name"], "interior", element["footprint"]) for element in design["elements"]
            if element["kind"] in WALL_KINDS and element["bottom_mm"] < design["clearance_mm"]]


def design_conflicts(project, bodies):
    design = project.req["interior"]
    if not design:
        return []
    conflicts = []
    view = design["view"]
    eye = Point(view["eye_mm"][:2]) if view else None
    eye_z = view["eye_mm"][2] if view else 0
    for element in design["elements"]:
        if element["kind"] == "floor_finish":
            continue
        shape, bottom, top = (element[k] for k in ("footprint", "bottom_mm", "top_mm"))
        if eye is not None and bottom - 70 < eye_z < top + 70 and shape.buffer(70).covers(eye):
            conflicts.append({"element": element["name"], "item": "人视机位"})
        for index, body in enumerate(bodies, 1):
            if min(top, body["base"] + body["height"]) <= max(bottom, body["base"]):
                continue
            if shape.intersection(Polygon(body["polygon"])).area > .01:
                conflicts.append({"element": element["name"], "item": index, "kind": body["kind"]})
    if eye is not None and any(body["base"] - 70 <= eye_z <= body["base"] + body["height"] + 70
                               and Polygon(body["polygon"]).buffer(70).covers(eye) for body in bodies):
        conflicts.append({"element": "人视机位", "item": "家具实体"})
    return conflicts
