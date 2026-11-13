"""Project parsing and geometry in millimetres."""

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from re import fullmatch

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union


class InputError(ValueError):
    pass


def read_json(path):
    with Path(path).open(encoding="utf-8-sig") as file:
        return json.load(file)


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(data):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def object_fields(value, allowed, context):
    if not isinstance(value, dict):
        raise InputError(f"{context} 应为对象")
    extra = value.keys() - set(allowed.split())
    if extra:
        raise InputError(f"{context} 有未识别字段：{', '.join(sorted(extra))}")
    return value


def number(value, label, low, high, *, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise InputError(f"{label} 应为 {low} 至 {high} 的数值")
    if integer and value != int(value):
        raise InputError(f"{label} 应为整数")
    return int(value) if integer else float(value)


def label(value, name, default=None):
    value = default if value is None else value
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise InputError(f"{name} 应为 1–200 字符的文本")
    return value.strip()


def flag(value, name):
    if not isinstance(value, bool):
        raise InputError(f"{name} 应为 true 或 false")
    return value


def color_hex(value, name):
    if not isinstance(value, str) or not fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise InputError(f"{name}应为 #RRGGBB 色值")
    return value.lower()


def points(value, name, min_length=3, max_length=400):
    if not isinstance(value, list) or not min_length <= len(value) <= max_length:
        raise InputError(f"{name} 应包含 {min_length} 至 {max_length} 个坐标")
    result = []
    for index, point in enumerate(value):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise InputError(f"{name} 第 {index + 1} 点应为 [x, y]")
        result.append(tuple(number(v, name, -100_000_000, 100_000_000) for v in point))
    return result


def polygon(value, name):
    shape = Polygon(points(value, name))
    if not shape.is_valid or shape.area < 1:
        raise InputError(f"{name} 多边形无效。请检查自交、重复边或退化轮廓")
    return shape


def as_ring(shape):
    return [[round(x, 6), round(y, 6)] for x, y in list(shape.exterior.coords)[:-1]]


def cents(value, name):
    if type(value) not in (str, int, float):
        raise InputError(f"{name} 应为以元计的非负金额")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 <= amount <= 10_000_000_000:
            raise InvalidOperation
        return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as exc:
        raise InputError(f"{name} 应为以元计的非负金额") from exc


@dataclass
class Opening:
    name: str
    line: LineString
    normal: tuple[float, float]
    throat_mm: float = 0
    sill_mm: float | None = None
    head_mm: float | None = None

    def point(self, distance):
        center = self.line.centroid
        return Point(center.x + self.normal[0] * distance, center.y + self.normal[1] * distance)

    def vestibule(self, depth):
        a, b = self.line.coords
        dx, dy = self.normal[0] * depth, self.normal[1] * depth
        return Polygon([a, b, (b[0] + dx, b[1] + dy), (a[0] + dx, a[1] + dy)])


def opening(record, boundary, name, require_inward):
    object_fields(record, "name segment throat_mm head_mm" if require_inward else "name segment sill_mm head_mm", name)
    xy = points(record.get("segment"), f"{name}线段", 2, 2)
    line = LineString(xy)
    if not 100 <= line.length <= 15_000 or not boundary.exterior.buffer(0.5).covers(line):
        raise InputError(f"{name}须落在已确认的边界边线上，并提供开口两端")
    (ax, ay), (bx, by) = xy
    nx, ny = -(by - ay) / line.length, (bx - ax) / line.length
    throat = number(record.get("throat_mm", 0), f"{name}已确认门洞窄段深度 mm", 0, 1000)
    sill, head = record.get("sill_mm"), record.get("head_mm")
    if not require_inward and (sill is None) != (head is None):
        raise InputError(f"{name} 应同时提供窗台与窗顶标高")
    if sill is not None:
        sill = number(sill, f"{name}窗台标高 mm", 0, 20_000)
    if head is not None:
        head = number(head, f"{name}开口顶部标高 mm", 300, 20_000)
    if sill is not None and head <= sill:
        raise InputError(f"{name}窗顶标高须高于窗台")
    result = Opening(label(record.get("name"), name, name), line, (nx, ny), throat, sill, head)
    if not boundary.contains(result.point(5)):
        result.normal = (-nx, -ny)
    if require_inward and not boundary.contains(result.point(5)):
        raise InputError(f"无法判断{name}的室内侧")
    return result


@dataclass
class Project:
    raw: dict
    title: str
    outline: Polygon
    fixed: list
    clear: list
    exits: list[Opening]
    windows: list[Opening]
    req: dict
    prices: dict
    height: float | None
    ready: bool
    voids: list


PRICE_KEYS = {
    "desk", "office_chair", "screen", "meeting_table_4", "meeting_table_6", "meeting_table_8",
    "meeting_chair", "pantry", "storage", "sofa", "coffee_table", "flooring_m2",
}

SERVICE_FURNITURE = {
    "waste_bin": ("日常垃圾桶", 420, 380, 620, 650),
    "print_station": ("打印与扫描站", 1050, 550, 1150, 850),
    "cleaning_cabinet": ("保洁备品柜", 760, 500, 1750, 800),
    "locker_bank": ("分格储物柜", 1200, 450, 1650, 800),
    "charging_station": ("共享充电站", 780, 380, 1020, 800),
}

FURNITURE = {
    # (name, width, depth, height, front use space), all dimensions in mm.
    "bed": ("床", 1600, 2000, 560, 700),
    "dining": ("四座餐桌组", 1400, 800, 750, 850),
    "display": ("展示架", 1200, 450, 1800, 800),
    "counter": ("服务台", 1800, 650, 1000, 900),
    "equipment": ("设备", None, None, None, None),
    "focus_pod": ("单人专注舱", 1300, 1450, 2250, 950),
    "workshop": ("共创工作台与白板", 2000, 1000, 740, 850),
    "planter": ("绿植隔断", 1200, 420, 1350, 550),
    "gallery": ("展陈装置", 1850, 450, 1750, 1000),
    "water_station": ("饮水与杯具站", 900, 440, 1200, 900),
    "recycling_station": ("分类回收柜", 840, 400, 780, 750),
    "entry_organizer": ("入口随身物收纳", 1040, 390, 1660, 800),
    "potted_plant": ("落地盆栽", 580, 580, 1450, 550),
    "wardrobe": ("衣柜", 1500, 580, 2120, 750),
    **SERVICE_FURNITURE,
}
PRIORITIES = {"comfort": "通行空间", "cost": "已报价预算", "privacy": "入口视线",
              "visitor": "访客区接近入口", "focus": "专注与活跃区间隔"}
STYLES = ("自然木色", "冷灰极简", "暖白轻商务", "自然疗愈", "工业创客", "当代展廊", "科技感", "石墨科技", "简约", "绿化")


def parse_furniture(records):
    if not isinstance(records, list) or len(records) > 30:
        raise InputError("furniture 应为最多 30 项的家具列表")
    result = {}
    for index, record in enumerate(records, 1):
        object_fields(record, "kind name count width_mm depth_mm height_mm access_mm access_sides unit_price_cny placement color_hex", "家具")
        kind = record.get("kind")
        if not isinstance(kind, str) or kind not in FURNITURE:
            raise InputError(f"家具 kind 应为 {' / '.join(FURNITURE)}")
        name, width, depth, height, access = FURNITURE[kind]
        row = {"kind": kind, "name": label(record.get("name"), "家具名称", name),
               "count": number(record.get("count", 1), "家具数量", 1, 30, integer=True)}
        if "color_hex" in record:
            row["color_hex"] = color_hex(record["color_hex"], f"{row['name']}的颜色")
        for field, value, low, high in (
            ("width_mm", width, 200, 8000), ("depth_mm", depth, 200, 8000),
            ("height_mm", height, 50, 6000), ("access_mm", access, 200, 4000),
        ):
            row[field] = number(record.get(field, value), f"{row['name']} {field}", low, high, integer=True)
        if kind == "dining" and (row["width_mm"] < 1000 or row["access_mm"] < 650):
            raise InputError("四座餐桌组的台面宽度至少 1000 mm，两排座椅使用带各至少 650 mm")
        fixed_sides = {"workshop": 2, "focus_pod": 1}
        if kind == "dining":
            if "access_sides" in record:
                raise InputError("四座餐桌组使用固定的前后两排，单侧与双侧选项用于其他家具")
        elif kind in fixed_sides:
            sides = fixed_sides[kind]
            if "access_sides" in record:
                number(record["access_sides"], f"{row['name']} 使用面数量", sides, sides, integer=True)
            row["access_sides"] = sides
        else:
            row["access_sides"] = number(record.get("access_sides", 1), "使用面数量", 1, 2, integer=True)
        row["unit_cents"] = None if record.get("unit_price_cny") is None else cents(record["unit_price_cny"], "家具单价")
        if "placement" in record:
            if row["count"] != 1:
                raise InputError(f"{row['name']} 固定位置要求 count 为 1，可将每件家具分别列出")
            placement = object_fields(record["placement"], "x y angle", f"{row['name']} 固定位置")
            angle = number(placement.get("angle"), "固定位置角度", 0, 270, integer=True)
            if angle not in (0, 90, 180, 270):
                raise InputError("固定位置角度应为 0、90、180 或 270")
            row["placement"] = {axis: number(placement.get(axis), f"固定位置 {axis} mm", -1e8, 1e8)
                                for axis in ("x", "y")}
            row["placement"]["angle"] = angle
        result[f"furniture_{index}"] = row
    return result


def parse_access_constraints(req, boundary, exits, require_confirmed):
    for key, allowed in (("clear_routes", "name points width_mm"), ("required_approaches", "name point entry")):
        records = req.get(key, [])
        if not isinstance(records, list) or len(records) > 24:
            raise InputError(f"{key} 应为最多 24 项的列表")
        parsed = {}
        for record in records:
            object_fields(record, allowed, key)
            name = label(record.get("name"), key)
            if name in parsed:
                raise InputError(f"{key} 名称重复：{name}")
            if key == "clear_routes":
                vertices = points(record.get("points"), name, 2, 80)
                line = LineString(vertices)
                if not line.is_simple or len(set(vertices)) != len(vertices) or line.length < 10:
                    raise InputError(f"{name} 中心线有自交、重复点或长度不足")
                width = number(record.get("width_mm"), f"{name} 净宽 mm", 600, 4000, integer=True)
                strip = line.buffer(width / 2, cap_style="flat", quad_segs=32)
                if strip.geom_type != "Polygon" or strip.interiors:
                    raise InputError(f"{name} 的净空带围成闭环，请按走道分段录入")
                parsed[name] = (line, width, strip)
            else:
                position, = points([record.get("point")], name, 1, 1)
                point = Point(position)
                if not boundary.contains(point):
                    raise InputError(f"{name} 的门前检测点应在净使用轮廓内")
                entry = record.get("entry")
                if entry is not None:
                    entry = label(entry, f"{name} 指定入口")
                    if require_confirmed and sum(e.name == entry for e in exits) != 1:
                        raise InputError(f"{name} 的指定入口须匹配唯一的已确认入口名称")
                parsed[name] = (point, entry)
        req[key] = parsed


def work_groups(records, boundary):
    if not isinstance(records, list) or len(records) > 12:
        raise InputError("work_groups 应为最多 12 个分区的列表")
    groups = {}
    for row in records:
        object_fields(row, "name desks polygon", "团队分区")
        name = label(row.get("name"), "团队分区名称")
        if name in groups:
            raise InputError(f"团队分区名称重复：{name}")
        shape = polygon(row.get("polygon"), name)
        if not boundary.covers(shape):
            raise InputError(f"团队分区 {name} 越出可用楼板或覆盖洞口")
        if any(shape.intersection(other["polygon"]).area > .01 for other in groups.values()):
            raise InputError(f"团队分区 {name} 与另一团队分区重叠")
        groups[name] = {"desks": number(row.get("desks"), f"{name} 工位数", 1, 200, integer=True),
                        "polygon": shape}
    return groups


def parse(raw, *, require_confirmed=True):
    object_fields(raw, "schema title geometry requirements prices", "项目")
    if raw.get("schema") != 1:
        raise InputError("项目 schema 应为 1")
    title = label(raw.get("title"), "项目名称")
    g = object_fields(raw.get("geometry"), "unit source confirmed outline voids fixed keep_clear exits windows ceiling_mm", "geometry")
    if g.get("unit") != "mm":
        raise InputError("统一几何单位为 mm。DXF 请先导入，图像请先标定")
    label(g.get("source"), "几何来源")
    ready = flag(g.get("confirmed"), "geometry.confirmed")
    if require_confirmed and not ready:
        raise InputError("请确认轮廓、门窗、出口、柱及不可移动结构后，再将 geometry.confirmed 设为 true")
    shell = polygon(g.get("outline"), "净使用轮廓")
    minx, miny, maxx, maxy = shell.bounds
    if shell.area > 2_500_000_000 or max(maxx - minx, maxy - miny) > 90_000:
        raise InputError("单层计算范围为 2,500 平方米以内，且任一方向跨度不超过 90 米")
    raw_voids = g.get("voids", [])
    if not isinstance(raw_voids, list) or len(raw_voids) > 24:
        raise InputError("voids 应为最多 24 个楼板洞口的列表")
    voids = {}
    for row in raw_voids:
        object_fields(row, "name polygon", "楼板洞口")
        name = label(row.get("name"), "楼板洞口名称")
        shape = polygon(row.get("polygon"), name)
        if (name in voids or not shell.contains(shape) or shell.boundary.intersects(shape)
                or any(shape.intersects(other) for other in voids.values())):
            raise InputError(f"洞口 {name} 必须位于外轮廓内部，且名称、边界和面积与其他洞口分开")
        voids[name] = shape
    boundary = Polygon(shell.exterior.coords, [shape.exterior.coords for shape in voids.values()])

    def feature_list(key, allowed):
        records = g.get(key, [])
        if not isinstance(records, list) or len(records) > 160:
            raise InputError(f"{key} 应为最多 160 个元素的列表")
        result = []
        for i, item in enumerate(records, 1):
            name = f"{key} {i}"
            object_fields(item, "name kind polygon", name)
            kind = item.get("kind")
            if kind not in allowed:
                raise InputError(f"{name} kind 应为 {' / '.join(sorted(allowed))}")
            shape = polygon(item.get("polygon"), name)
            if not boundary.buffer(0.5).covers(shape):
                raise InputError(f"{name} 越出轮廓，请修正坐标")
            result.append((label(item.get("name"), name, name), kind, shape))
        return result

    fixed = feature_list("fixed", {"column", "wall", "service", "existing"})
    clear = feature_list("keep_clear", {"door_swing", "reserved", "no_walk"})
    exits, windows = [], []
    for key, collection in [("exits", exits), ("windows", windows)]:
        records = g.get(key, [])
        if not isinstance(records, list) or len(records) > 60:
            raise InputError(f"{key} 应为最多 60 条开口的列表")
        for i, item in enumerate(records, 1):
            edge = opening(item, boundary, f"{'出口' if key == 'exits' else '窗'} {i}", key == "exits")
            if any(edge.line.intersection(other.line).length > 1 for other in exits + windows):
                raise InputError(f"{edge.name} 与另一开口重叠")
            collection.append(edge)
    if require_confirmed and not exits:
        raise InputError("至少确认一个实际可用的出口，并给出开口两端")
    height = g.get("ceiling_mm")
    if height is not None:
        height = number(height, "层高 mm", 1900, 20_000)
        if any(o.head_mm is not None and o.head_mm > height for o in exits + windows):
            raise InputError("开口顶部标高高于已确认净高")

    req = object_fields(raw.get("requirements"), "desks meeting_seats pantry storage lounge aisle_mm exit_min_mm options budget_limit_cny budget_mode max_travel_mm style zones work_groups furniture priorities visitor_entries clear_routes required_approaches interior", "requirements").copy()
    from interior import parse_interior
    req["interior"] = parse_interior(req.get("interior"), boundary, fixed, clear, exits + windows, height)
    req["furniture"] = parse_furniture(req.get("furniture", []))
    req["work_groups"] = work_groups(req.get("work_groups", []), boundary)
    group_count = sum(group["desks"] for group in req["work_groups"].values())
    req["desks"] = number(req.get("desks", group_count), "工位数", 0, 200, integer=True)
    if req["work_groups"] and req["desks"] != group_count:
        raise InputError(f"工位总数 {req['desks']} 与团队分区合计 {group_count} 不符")
    req["aisle_mm"] = number(req.get("aisle_mm"), "项目通行净宽 mm", 600, 3000, integer=True)
    req["exit_min_mm"] = number(req.get("exit_min_mm", req["aisle_mm"]), "项目出口最小净宽 mm", 500, 3000, integer=True)
    priorities = req.get("priorities", [])
    if (not isinstance(priorities, list) or any(not isinstance(x, str) or x not in PRIORITIES for x in priorities)
            or len(set(priorities)) != len(priorities)):
        raise InputError(f"priorities 应为不重复的 {' / '.join(PRIORITIES)} 列表")
    req["priorities"] = priorities
    visitor_entries = req.get("visitor_entries", [])
    if (not isinstance(visitor_entries, list) or any(not isinstance(x, str) or not x.strip() for x in visitor_entries)
            or len(set(visitor_entries)) != len(visitor_entries)):
        raise InputError("visitor_entries 应为不重复的出口名称列表")
    if require_confirmed and any(sum(o.name == entry for o in exits) != 1 for entry in visitor_entries):
        raise InputError("访客入口须匹配唯一的已确认出口名称")
    req["visitor_entries"] = visitor_entries
    parse_access_constraints(req, boundary, exits, require_confirmed)
    req["options"] = number(req.get("options", 3), "方案数", 1, 3, integer=True)
    req["style"] = label(req.get("style"), "风格", "自然木色")
    if req["style"] not in STYLES:
        raise InputError(f"风格可选 {' / '.join(STYLES)}")
    meetings = req.setdefault("meeting_seats", [])
    if not isinstance(meetings, list) or len(meetings) > 6 or any(type(n) is not int or n not in (4, 6, 8) for n in meetings):
        raise InputError("meeting_seats 应为最多六项的列表，每项为 4、6 或 8")
    for name, maximum in [("pantry", 4), ("storage", 12), ("lounge", 6)]:
        req[name] = number(req.get(name, 0), name, 0, maximum, integer=True)
    count = req["desks"] + len(meetings) + sum(req[k] for k in ("pantry", "storage", "lounge"))
    count += sum(row["count"] for row in req["furniture"].values())
    if not 1 <= count <= 240:
        raise InputError("请提供至少一项空间用途配置，单层家具组与工位合计上限为 240")
    zones = req.get("zones", [])
    if not isinstance(zones, list) or len(zones) > 30:
        raise InputError("zones 应为最多 30 个分区的列表")
    by_use = {}
    for zone in zones:
        object_fields(zone, "use polygon", "指定使用区")
        use = zone.get("use")
        if use not in ("work", "meeting", "pantry", "storage", "lounge", *FURNITURE):
            raise InputError(f"分区 use 应为 work / meeting / pantry / storage / lounge / {' / '.join(FURNITURE)}")
        shape = polygon(zone.get("polygon"), "指定使用区")
        if not boundary.covers(shape):
            raise InputError("指定使用区越出净使用轮廓")
        by_use.setdefault(use, []).append(shape)
    req["zones"] = {key: unary_union(shapes) for key, shapes in by_use.items()}
    limit = req.get("max_travel_mm")
    if limit is not None:
        req["max_travel_mm"] = number(limit, "项目通行路径长度上限 mm", 1000, 250_000)
    budget = req.get("budget_limit_cny")
    req["budget_mode"] = req.get("budget_mode", "reference")
    if req["budget_mode"] not in ("reference", "cap"):
        raise InputError("budget_mode 应为 reference（对照）或 cap（已量化项目硬上限）")
    if req["budget_mode"] == "cap" and budget is None:
        raise InputError("使用预算硬上限须提供 budget_limit_cny")
    req["budget_cents"] = None if budget is None else cents(budget, "预算上限")
    price = object_fields(raw.get("prices", {}), "source date unit_prices", "prices")
    unit_prices = object_fields(price.get("unit_prices", {}), " ".join(PRICE_KEYS), "unit_prices")
    if unit_prices or any(row["unit_cents"] is not None for row in req["furniture"].values()):
        label(price.get("source"), "价格来源")
        label(price.get("date"), "价格日期")
    return Project(raw, title, boundary, fixed, clear, exits, windows, req,
                   {key: cents(value, key) for key, value in unit_prices.items()}, height, ready, list(voids.items()))
