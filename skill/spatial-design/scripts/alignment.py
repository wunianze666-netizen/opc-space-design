"""Compare DXF geometry and shared 3D scene geometry against layout coordinates."""

from collections import Counter, defaultdict
from math import fsum, isfinite, radians

import ezdxf
from shapely import union_all
from shapely.geometry import Point, Polygon

from interior import design_layer
from layout import footprint, pieces
from model import InputError
from visual import EXTERIOR_WALL_MM, PALETTES, rgb, scene, scene_height


FIXED_LAYERS = {
    "column": "OPC_COLUMN", "wall": "OPC_FIXED_WALL", "service": "OPC_SERVICE",
    "existing": "OPC_FIXED", "door_swing": "OPC_DOOR_SWING",
    "reserved": "OPC_KEEP_CLEAR", "no_walk": "OPC_NO_WALK",
}
PRECISION = 6
SCENE_TOLERANCE_MM = 1e-6


def coordinates(point):
    return tuple(round(float(value), PRECISION) or 0.0 for value in point)


def polygon_key(points):
    shape = Polygon([coordinates(point) for point in points])
    if not shape.is_valid or not shape.area:
        raise InputError("闭合多段线形成了无效多边形")
    return "polygon", tuple(shape.normalize().exterior.coords)[:-1]


def line_key(points):
    path = tuple(coordinates(point) for point in points)
    return "line", min(path, path[::-1])


def reference_layers(project, placements, bodies):
    layers = defaultdict(list)
    layers["OPC_OUTLINE"].append(polygon_key(project.outline.exterior.coords))
    for _, void in project.voids:
        layers["OPC_VOID"].append(polygon_key(void.exterior.coords))
    for _, kind, shape in project.fixed + project.clear:
        layers[FIXED_LAYERS[kind]].append(polygon_key(shape.exterior.coords))
    for layer, openings in (("OPC_EXIT", project.exits), ("OPC_WINDOW", project.windows)):
        for opening in openings:
            layers[layer].append(line_key(opening.line.coords))
    for line, _, strip in project.req["clear_routes"].values():
        layers["OPC_ROUTE_CENTER"].append(line_key(line.coords))
        layers["OPC_PROTECTED_ROUTE"].append(polygon_key(strip.exterior.coords))
    for name, (point, _) in project.req["required_approaches"].items():
        xy = coordinates(point.coords[0])
        layers["OPC_REQUIRED_APPROACH"].extend([("point", xy), ("text", name, xy)])
    for name, group in project.req["work_groups"].items():
        layers["OPC_WORK_GROUP"].append(polygon_key(group["polygon"].exterior.coords))
        layers["OPC_WORK_GROUP_LABEL"].append(("text", name))
    if project.req["interior"]:
        for element in project.req["interior"]["elements"]:
            shape = element["footprint"]
            layers[design_layer(element["kind"])].append(polygon_key(shape.exterior.coords))
            layers["OPC_INTERIOR_LABEL"].append(("text", element["name"], coordinates(shape.representative_point().coords[0])))
    for placement in placements:
        layers["OPC_USAGE_ENVELOPE"].append(polygon_key(footprint(placement, project).exterior.coords))
    for item in bodies:
        layers["FURN_" + item["kind"].upper()].append(polygon_key(item["polygon"]))
    return layers


def xy_plane(point):
    if not all(isfinite(value) for value in point) or point.z != 0:
        raise InputError("应位于 DXF 的 XY 平面")
    return coordinates((point.x, point.y))


def entity_key(entity, layer):
    kind = entity.dxftype()
    if kind == "LWPOLYLINE":
        if tuple(entity.dxf.extrusion) != (0, 0, 1) or entity.dxf.elevation or entity.dxf.const_width:
            raise InputError("多段线平面或宽度与导出约定不符")
        vertices = entity.get_points("xyseb")
        if any(not isfinite(x) or not isfinite(y) or start or end or bulge
               for x, y, start, end, bulge in vertices):
            raise InputError("多段线含无效坐标、宽度或曲线段")
        points = [(x, y) for x, y, *_ in vertices]
        if entity.closed:
            return polygon_key(points)
        return line_key(points)
    if kind == "LINE":
        return line_key((xy_plane(entity.dxf.start), xy_plane(entity.dxf.end)))
    if kind == "POINT":
        return "point", xy_plane(entity.dxf.location)
    if kind == "TEXT":
        if layer == "OPC_WORK_GROUP_LABEL":
            return "text", entity.dxf.text
        return "text", entity.dxf.text, xy_plane(entity.dxf.insert)
    raise InputError(f"存在不支持的图元 {kind}")


def compare_dxf(project, placements, bodies, dxf_path):
    try:
        doc = ezdxf.readfile(dxf_path)
    except (OSError, ezdxf.DXFError) as exc:
        raise InputError(f"无法读取布局 DXF {dxf_path}: {exc}") from exc
    if doc.units != ezdxf.units.MM:
        raise InputError("布局 DXF 的单位应为毫米")
    expected = reference_layers(project, placements, bodies)
    actual = defaultdict(list)
    for entity in doc.modelspace():
        layer = entity.dxf.layer.upper()
        if layer == "OPC_DIMENSIONS" and entity.dxftype() in ("DIMENSION", "TEXT"):
            continue
        try:
            actual[layer].append(entity_key(entity, layer))
        except (InputError, ValueError, ezdxf.DXFError) as exc:
            raise InputError(f"DXF 图层 {layer} 无法核对: {exc}") from exc
    for layer in sorted(set(expected) | set(actual)):
        wanted, found = Counter(expected[layer]), Counter(actual[layer])
        if wanted != found:
            absent = next(iter((wanted - found).elements()), None)
            extra = next(iter((found - wanted).elements()), None)
            sample = f"参考 {str(absent)[:155]}。图中 {str(extra)[:155]}。"
            raise InputError(f"DXF 图层 {layer} 与布局坐标或数量不一致。{sample}")
        if layer not in doc.layers or doc.layers.get(layer).is_off() or doc.layers.get(layer).is_frozen():
            raise InputError(f"DXF 图层 {layer} 缺失、已关闭或已冻结")
    geometry = {layer: sorted(entries) for layer, entries in sorted(actual.items())}
    return {"counts_by_layer": {layer: len(entries) for layer, entries in geometry.items()},
            "geometry_by_layer": geometry}


def scene_coordinates(vertices, description):
    if len(vertices) < 3 or any(len(point) != 3 or not all(isfinite(v) for v in point) for point in vertices):
        raise InputError(f"三维场景 {description} 包含无效面坐标")
    return vertices


def floor_triangles(faces, owners, expected_floor):
    triangles = []
    for (vertices, _), owner in zip(faces, owners, strict=True):
        if owner != ("floor", 0):
            continue
        vertices = scene_coordinates(vertices, "楼板")
        if len(vertices) != 3 or any(abs(z) > SCENE_TOLERANCE_MM for _, _, z in vertices):
            raise InputError("三维楼板表面应由位于 Z=0 的三角面组成")
        triangle = Polygon([(x, y) for x, y, _ in vertices])
        if not triangle.is_valid or not triangle.area:
            raise InputError("三维楼板存在退化三角面")
        triangles.append(triangle)
    floor = union_all(triangles)
    if not floor.equals(expected_floor):
        difference = floor.symmetric_difference(expected_floor).area
        raise InputError(f"三维楼板与净轮廓或洞口不一致，差异面积 {difference:g} mm²")
    if abs(fsum(triangle.area for triangle in triangles) - floor.area) > max(1e-4, floor.area * 1e-12):
        raise InputError("三维楼板表面存在重叠三角面")
    return triangles


def verify_furniture(faces, owners, bodies):
    furniture_faces = defaultdict(list)
    for (vertices, _), owner in zip(faces, owners, strict=True):
        if owner is not None and owner[0] == "furniture":
            index = owner[1]
            if type(index) is not int or not 0 <= index < len(bodies):
                raise InputError(f"三维场景中存在无法对应的家具编号 {index}")
            furniture_faces[index].append(vertices)
    evidence = []
    for index, body in enumerate(bodies):
        description = f"家具 {index + 1}（{body['kind']}）"
        parts = furniture_faces[index]
        if not parts:
            raise InputError(f"三维场景缺少{description}")
        # Physical pieces are rectangular. Their bounds are exact because rotations are cardinal.
        footprint = Polygon(body["polygon"])
        x0, y0, x1, y1 = footprint.bounds
        z0, z1 = body["base"], body["base"] + body["height"]
        limits = ((x0, x1), (y0, y1), (z0, z1))
        vertices = [point for part in parts for point in scene_coordinates(part, description)]
        for point in vertices:
            if any(value < low - SCENE_TOLERANCE_MM or value > high + SCENE_TOLERANCE_MM
                   for value, (low, high) in zip(point, limits, strict=True)):
                raise InputError(f"三维{description}的面坐标 {coordinates(point)} 超出设计范围 {limits}")
        axes = list(zip(*vertices, strict=True))
        evidence.append({"piece": index + 1, "kind": body["kind"],
                         "footprint_mm": polygon_key(body["polygon"])[1],
                         "design_z_mm": coordinates((z0, z1)), "face_count": len(parts),
                         "bounds_xyz_mm": [coordinates((min(axis), max(axis))) for axis in axes]})
    return evidence


def verify_structure(project, faces, owners):
    by_owner = defaultdict(list)
    for (vertices, _), owner in zip(faces, owners, strict=True):
        if owner is not None and owner[0] in ("fixed", "window", "exit_header"):
            by_owner[owner].append(vertices)
    height = scene_height(project)
    tolerance = SCENE_TOLERANCE_MM
    for index, (name, kind, shape) in enumerate(project.fixed):
        top = height if kind in ("column", "wall") else min(height, 1550)
        faces_ = by_owner[("fixed", index)]
        surface = union_all([Polygon([(x, y) for x, y, _ in part])
                             for part in faces_ if all(abs(z - top) <= tolerance for _, _, z in part)])
        if not surface.buffer(tolerance).covers(shape) or not shape.buffer(tolerance).covers(surface):
            raise InputError(f"三维固定结构 {name} 与平面轮廓不一致")
        expanded = shape.buffer(tolerance)
        if any(not expanded.covers(Point(x, y)) or not -tolerance <= z <= top + tolerance
               for part in faces_ for x, y, z in scene_coordinates(part, name)):
            raise InputError(f"三维固定结构 {name} 的坐标超出平面或高度范围")
    for category, openings in (("window", project.windows), ("exit_header", project.exits)):
        for index, opening in enumerate(openings):
            if opening.head_mm is None:
                continue
            expected_z = ((opening.sill_mm, opening.head_mm) if category == "window"
                          else (opening.head_mm, height))
            parts = by_owner[(category, index)]
            if expected_z[0] == expected_z[1] and not parts:
                continue
            vertices = [point for part in parts for point in scene_coordinates(part, opening.name)]
            if not vertices:
                raise InputError(f"三维开口 {opening.name} 缺少标高对应面")
            (x0, y0), (x1, y1) = opening.line.coords
            length = opening.line.length
            along = [((x - x0) * (x1 - x0) + (y - y0) * (y1 - y0)) / length for x, y, _ in vertices]
            inward = [(x - x0) * opening.normal[0] + (y - y0) * opening.normal[1] for x, y, _ in vertices]
            z_values = [z for _, _, z in vertices]
            if any(abs(a - b) > tolerance for a, b in zip(
                    (min(along), max(along), min(inward), max(inward), min(z_values), max(z_values)),
                    (0, length, -EXTERIOR_WALL_MM, 0, *expected_z), strict=True)):
                raise InputError(f"三维开口 {opening.name} 的宽度、位置或标高不一致")


def verify_interior(project, faces, owners):
    design = project.req["interior"]
    if not design:
        return []
    elements = design["elements"]
    floor_faces = defaultdict(list)
    interior_faces = defaultdict(list)
    for (vertices, color), owner in zip(faces, owners, strict=True):
        if owner == ("floor", 0):
            floor_faces[color].append(Polygon([(x, y) for x, y, _ in vertices]))
        elif owner is not None and owner[0] == "interior":
            index = owner[1]
            if type(index) is not int or not 0 <= index < len(elements):
                raise InputError("三维室内元素的归属编号无效")
            interior_faces[index].append(vertices)
    expected_finishes = defaultdict(list)
    for element in elements:
        if element["kind"] == "floor_finish":
            expected_finishes[rgb(element["color"])].append(element["footprint"])
    if expected_finishes:
        patches = union_all([shape for group in expected_finishes.values() for shape in group])
        default_color = rgb(PALETTES[project.req["style"]]["floor"])
        expected_finishes[default_color].append(project.outline.difference(patches))
        for color in expected_finishes.keys() | floor_faces.keys():
            expected = union_all(expected_finishes[color])
            actual = union_all(floor_faces[color])
            if expected.is_empty and actual.is_empty:
                continue
            if not expected.buffer(1e-5).covers(actual) or not actual.buffer(1e-5).covers(expected):
                raise InputError("三维地面饰面的材质分界与设计范围不一致")
    evidence = []
    for index, element in enumerate(elements):
        shape = element["footprint"]
        name, kind, material = (element[k] for k in ("name", "kind", "material"))
        if kind != "floor_finish":
            parts = interior_faces[index]
            vertices = [v for part in parts for v in scene_coordinates(part, name)]
            if not vertices:
                raise InputError(f"三维场景缺少室内元素 {name}")
            expanded = shape.buffer(1e-5)
            z = [point[2] for point in vertices]
            if (any(not expanded.covers(Point(x, y)) for x, y, _ in vertices)
                    or abs(min(z) - element["bottom_mm"]) > 1e-5 or abs(max(z) - element["top_mm"]) > 1e-5):
                raise InputError(f"三维室内元素 {name} 的平面范围或标高不一致")
        evidence.append({"name": name, "kind": kind, "material": material, "area_m2": round(shape.area / 1e6, 4),
                         "footprint_mm": polygon_key(shape.exterior.coords)[1],
                         "bottom_mm": element["bottom_mm"], "top_mm": element["top_mm"]})
    return evidence


def compare_scene(project, placements, bodies):
    expected_floor = Polygon(project.outline.exterior.coords,
                             [shape.exterior.coords for _, shape in project.voids])
    if not expected_floor.equals(project.outline):
        raise InputError("项目净轮廓与命名楼板洞口不一致")
    results = {}
    for name, azimuth in (("model", None), ("axon_45", radians(45)), ("axon_225", radians(225))):
        owners = []
        faces = scene(project, placements, azimuth, owners=owners)
        if len(owners) != len(faces):
            raise InputError(f"三维场景 {name} 中的几何归属与面数量不一致")
        try:
            triangles = floor_triangles(faces, owners, expected_floor)
            furniture = verify_furniture(faces, owners, bodies)
            if name == "model":
                verify_structure(project, faces, owners)
                fittings = verify_interior(project, faces, owners)
        except InputError as exc:
            raise InputError(f"三维场景 {name} 核对失败: {exc}") from exc
        results[name] = {"floor_triangle_count": len(triangles),
                         "furniture_piece_count": len(furniture)}
        if name == "model":
            results[name]["furniture"] = furniture
            results[name]["floor_triangles_mm"] = sorted(polygon_key(shape.exterior.coords)[1] for shape in triangles)
            if fittings:
                results[name]["interior"] = fittings
    return {"floor_area_mm2": round(expected_floor.area, PRECISION),
            "floor_void_count": len(expected_floor.interiors), "views": results}


def verify_alignment(project, placements, dxf_path):
    """Reopen the DXF and check the shared model before reporting coordinate alignment."""
    bodies = pieces(placements, project)
    return {"passed": True, "units": "mm", "dxf": compare_dxf(project, placements, bodies, dxf_path),
            "scene": compare_scene(project, placements, bodies)}
