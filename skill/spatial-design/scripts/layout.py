"""Furniture templates, layout search and independent geometric checks."""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from heapq import heappop, heappush
from math import ceil, cos, dist, floor, pi

import numpy as np
import shapely
from shapely import prepare
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from interior import design_conflicts, walking_fittings
from model import InputError, PRIORITIES


STRATEGIES = {
    "open": ("开放高效", "优先成组工位，会议区靠近入口，减少访客穿越办公区。"),
    "hybrid": ("混合协作", "分布办公组团，将洽谈与共享家具布置在活动区域。"),
    "focus": ("半围合专注", "设置工位屏风与会议半围合，保留开口和通行空间。"),
}
GENERAL_STRATEGIES = {
    "open": ("开放布置", "家具以集中或沿边为主，保留可用的公共区域。"),
    "hybrid": ("功能分区", "将使用目的不同的家具分开布置，比较访客与日常动线。"),
    "focus": ("内外有别", "优先考虑私密家具与入口的关系，使用区域按项目需要保留。"),
}

NAMES = {
    "desk": "办公桌", "office_chair": "办公椅", "screen": "屏风",
    "meeting_table_4": "四人会议桌", "meeting_table_6": "六人会议桌",
    "meeting_table_8": "八人会议桌", "meeting_chair": "会议椅", "pantry": "茶水柜",
    "storage": "储物柜", "sofa": "双人沙发", "coffee_table": "茶几", "flooring_m2": "地面饰材",
}


@dataclass(frozen=True)
class Template:
    width: int
    depth: int
    pieces: tuple
    ports: tuple
    desks: int = 0


def piece(kind, x, y, width, depth, height, base=0):
    return (kind, x, y, width, depth, height, base)


def desk_template(columns, paired, screens):
    pieces, ports = [], []
    for col in range(columns):
        x = col * 1400
        pieces.extend([piece("desk", x, 800, 1400, 700, 750),
                       piece("office_chair", x + 425, 200, 550, 570, 890)])
        ports.append((x + 700, 0, 0, -1))
        if paired:
            pieces.extend([piece("desk", x, 1500, 1400, 700, 750),
                           piece("office_chair", x + 425, 2230, 550, 570, 890)])
            ports.append((x + 700, 3000, 0, 1))
        if screens:
            pieces.append(piece("screen", x + 20, 1475, 1360, 25, 425, 750))
    return Template(columns * 1400, 3000 if paired else 1500, tuple(pieces), tuple(ports),
                    columns * (2 if paired else 1))


def meeting_template(seats, screens):
    length, depth = {4: 1800, 6: 2400, 8: 3000}[seats], 3500
    width = length + 1450
    pieces = [piece(f"meeting_table_{seats}", 725, 1300, length, 900, 740)]
    for i in range(seats // 2):
        x = 725 + (i + .5) * length / (seats // 2) - 260
        pieces.extend([piece("meeting_chair", x, 660, 520, 520, 870),
                       piece("meeting_chair", x, 2320, 520, 520, 870)])
    if screens:
        pieces.extend([piece("screen", 0, 50, 55, depth - 50, 1800),
                       piece("screen", width - 55, 50, 55, depth - 50, 1800)])
    ports = []
    for i in range(seats // 2):
        x = 725 + (i + .5) * length / (seats // 2)
        ports.extend([(x, 0, 0, -1), (x, depth, 0, 1)])
    return Template(width, depth, tuple(pieces), tuple(ports))


TEMPLATES = {
    "desk": desk_template(1, False, False), "desk2": desk_template(1, True, False),
    "desk4": desk_template(2, True, False), "focus1": desk_template(1, False, True),
    "focus2": desk_template(1, True, True),
    "pantry": Template(1800, 1500, (piece("pantry", 0, 900, 1800, 600, 920),),
                       ((900, 0, 0, -1),)),
    "storage": Template(1100, 1300, (piece("storage", 0, 850, 1100, 450, 1700),),
                        ((550, 0, 0, -1),)),
    "lounge": Template(2400, 2500, (piece("sofa", 250, 1520, 1900, 900, 820),
                                  piece("coffee_table", 730, 650, 940, 580, 420)),
                       ((1200, 0, 0, -1),)),
}
for _seats in (4, 6, 8):
    TEMPLATES[f"meeting{_seats}"] = meeting_template(_seats, False)
    TEMPLATES[f"meeting{_seats}s"] = meeting_template(_seats, True)


def strategy_text(project, strategy):
    if project.req["furniture"] and not project.req["desks"]:
        return GENERAL_STRATEGIES[strategy]
    return STRATEGIES[strategy]


@lru_cache(maxsize=256)
def furniture_template(kind, w, d, h, access, sides):
    if kind == "bed":
        left = access if sides == 2 else 0
        ports_ = [(left + w + access, d / 2, 1, 0)]
        if sides == 2:
            ports_.append((0, d / 2, -1, 0))
        return Template(left + w + access, d, (piece(kind, left, 0, w, d, h),), tuple(ports_))
    if kind == "dining":
        depth = d + 2 * access
        objects = [piece(kind, 0, access, w, d, h)]
        ports_ = []
        for center in (w / 4, 3 * w / 4):
            objects.extend([piece("dining_chair", center - 230, access - 510, 460, 470, 850),
                            piece("dining_chair", center - 230, access + d + 40, 460, 470, 850)])
            ports_.extend([(center, 0, 0, -1), (center, depth, 0, 1)])
        return Template(w, depth, tuple(objects), tuple(ports_))
    if kind == "workshop":
        depth = d + 2 * access
        objects = (piece(kind, 0, access, w, d, h),
                   piece("whiteboard", w + access, access, 70, d, 1650))
        ports_ = ((w / 2, 0, 0, -1), (w / 2, depth, 0, 1))
        return Template(w + access + 70, depth, objects, ports_)
    depth = d + sides * access
    ports_ = [(w / 2, 0, 0, -1)]
    if sides == 2:
        ports_.append((w / 2, depth, 0, 1))
    return Template(w, depth, (piece(kind, 0, access, w, d, h),), tuple(ports_))


def template(token, project=None):
    if token in TEMPLATES:
        return TEMPLATES[token]
    spec = project.req["furniture"].get(token) if project else None
    if spec is None:
        raise InputError(f"未知的家具类型：{token}")
    return furniture_template(spec["kind"], spec["width_mm"], spec["depth_mm"],
                              spec["height_mm"], spec["access_mm"], spec.get("access_sides", 1))


def rotated(x, y, w, d, rotation):
    if rotation == 0:
        return x, y
    if rotation == 90:
        return d - y, x
    if rotation == 180:
        return w - x, d - y
    return y, w - x


def transform(place, x, y, project=None):
    t = template(place["type"], project)
    px, py = rotated(x, y, t.width, t.depth, place["angle"])
    return place["x"] + px, place["y"] + py


def footprint(place, project=None):
    t = template(place["type"], project)
    w, d = (t.width, t.depth) if place["angle"] % 180 == 0 else (t.depth, t.width)
    return box(place["x"], place["y"], place["x"] + w, place["y"] + d)


def use_of(token, project=None):
    if project and token in project.req["furniture"]:
        return project.req["furniture"][token]["kind"]
    if template(token, project).desks:
        return "work"
    return "meeting" if token.startswith("meeting") else token


def pieces(placements, project=None):
    result = []
    for block in placements:
        t = template(block["type"], project)
        custom = project.req["furniture"].get(block["type"]) if project else None
        for kind, x, y, width, depth, height, base in t.pieces:
            corners = [transform(block, u, v, project) for u, v in
                       [(x, y), (x + width, y), (x + width, y + depth), (x, y + depth)]]
            result.append({"kind": kind, "polygon": corners, "height": height, "base": base})
            if custom and kind == custom["kind"] and "color_hex" in custom:
                result[-1]["color_hex"] = custom["color_hex"]
            if kind in ("office_chair", "meeting_chair"):
                result[-1]["back"] = "far" if y > 1500 else "near"
            elif kind == "dining_chair":
                result[-1]["back"] = "far" if y > t.depth / 2 else "near"
    return result


def ports(place, radius, project=None):
    result = []
    for x, y, dx, dy in template(place["type"], project).ports:
        anchor = transform(place, x, y, project)
        approach = transform(place, x + dx * (radius + 15), y + dy * (radius + 15), project)
        result.append((anchor, approach))
    return result


def components(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if hasattr(geometry, "geoms"):
        return [p for g in geometry.geoms for p in components(g)]
    return []


def walking_obstacles(project):
    return project.fixed + [row for row in project.clear if row[1] == "no_walk"] + walking_fittings(project)


def walking_regions(project, occupied, radius):
    fixed = [shape for _, _, shape in walking_obstacles(project)]
    blocked = unary_union(fixed + occupied)
    free = project.outline.buffer(-radius, quad_segs=8).difference(
        blocked.buffer(radius, quad_segs=8))
    return components(free), blocked


def clear_bridge(anchor, point, width, radius, shell, obstacles, own_index):
    connection = LineString([anchor, point])
    bridge = connection.buffer(radius, quad_segs=8)
    strip = connection.buffer(width / 2, cap_style="flat")
    return shell.covers(strip) and not any(index != own_index for index in obstacles.query(bridge, predicate="intersects"))


def check(id_, status, description, **extra):
    return {"id": id_, "status": status, "description": description, **extra}


def protected_route_check(project, occupied):
    issues = []
    measured = []
    for name, (_, width, shape) in project.req["clear_routes"].items():
        measured.append({"name": name, "width_mm": width})
        fixed = [label for label, _, item in walking_obstacles(project) if shape.intersection(item).area > .01]
        furniture = [i for i, item in enumerate(occupied, 1) if shape.intersection(item).area > .01]
        outside = not project.outline.covers(shape)
        if outside or fixed or furniture:
            issues.append({"name": name, "width_mm": width, "outside": outside, "fixed": fixed, "furniture": furniture})
    return check("protected_routes", "fail" if issues else "pass", "逐条核对指定净空通道",
                 routes=measured, conflicts=issues)


def locked_placements(project):
    return [{"type": token, **spec["placement"]} for token, spec in project.req["furniture"].items()
            if "placement" in spec]


def route_distances(region, sources, targets):
    """Conservative sampled route bounds. Every graph edge is inside free space."""
    x0, y0, x1, y1 = region.bounds
    step = 250
    if (x1 - x0) * (y1 - y0) / (step * step) > 16000:
        step = 400

    def xy(node):
        return x0 + step * node[0], y0 + step * node[1]

    def connectors(point):
        ix, iy = round((point[0] - x0) / step), round((point[1] - y0) / step)
        links = []
        for x in range(ix - 2, ix + 3):
            for y in range(iy - 2, iy + 3):
                node = (x, y)
                pos = xy(node)
                if region.covers(Point(pos)) and region.covers(LineString([point, pos])):
                    links.append((node, dist(point, pos)))
        return links

    target_links = [connectors(p) for p, _ in targets]
    needed = {node for links in target_links for node, _ in links}
    heap, distances, settled = [], {}, set()
    for start, entry_length in sources:
        for node, length in connectors(start):
            total = entry_length + length
            if total < distances.get(node, float("inf")):
                distances[node] = total
                heappush(heap, (total, node))
    directions = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
    while heap and needed:
        distance, node = heappop(heap)
        if node in settled:
            continue
        settled.add(node)
        needed.discard(node)
        for dx, dy in directions:
            nxt = (node[0] + dx, node[1] + dy)
            total = distance + step * (2 ** .5 if dx and dy else 1)
            if total < distances.get(nxt, float("inf")) and region.covers(LineString([xy(node), xy(nxt)])):
                distances[nxt] = total
                heappush(heap, (total, nxt))
    values = []
    for (end, tail), links in zip(targets, target_links):
        paths = [distances[node] + length + tail for node, length in links if node in settled]
        for start, entry_length in sources:
            if region.covers(LineString([start, end])):
                paths.append(entry_length + dist(start, end) + tail)
        values.append(min(paths, default=None))
    return values


def circulation(project, occupied, placements):
    width = project.req["aisle_mm"]
    # GEOS approximates round buffers with chords. Grow the obstacle radius
    # slightly so the shortest chord still provides the requested radius.
    radius = width / (2 * cos(pi / 32)) + 1
    static = [shape for _, _, shape in walking_obstacles(project)]
    obstacle_index = shapely.STRtree(static + occupied)
    regions, walls_and_furniture = walking_regions(project, occupied, radius)
    shell = project.outline.buffer(.5)
    entrances, entrance_names = {}, {}
    for index, region in enumerate(regions):
        prepare(region)
        entrances[index] = []
        entrance_names[index] = set()
    blocked_exits = []
    for exit_ in project.exits:
        probe = exit_.point(exit_.throat_mm + radius + 15)
        threshold = exit_.line.centroid
        transition = exit_.point(exit_.throat_mm)
        approach_line = LineString([transition, probe])
        strip = approach_line.buffer(width / 2, cap_style="flat")
        if exit_.throat_mm:
            throat = LineString([threshold, transition]).buffer(project.req["exit_min_mm"] / 2, cap_style="flat")
            strip = strip.union(throat)
            collision = strip.intersection(walls_and_furniture).area > .01
        else:
            collision = approach_line.buffer(radius, quad_segs=8).intersects(walls_and_furniture)
        matching = [i for i, region in enumerate(regions) if region.covers(probe)]
        if (exit_.line.length + .01 < project.req["exit_min_mm"] or collision
                or not shell.covers(strip) or not matching):
            blocked_exits.append(exit_.name)
            continue
        for i in matching:
            entrances[i].append(((probe.x, probe.y), threshold.distance(probe)))
            entrance_names[i].add(exit_.name)

    access_count = 0
    unreachable = []
    targets = {i: [] for i in entrances}
    for position, placement in enumerate(placements, 1):
        available = ports(placement, radius, project)
        # A workstation has one required access per seat. Other bundles need
        # one connection to their own reserved access envelope.
        separate = (template(placement["type"], project).desks or placement["type"].startswith("meeting")
                    or placement["type"] in project.req["furniture"])
        groups = [[port] for port in available] if separate else [available]
        for group in groups:
            matches = []
            for anchor, point in group:
                for i, region in enumerate(regions):
                    if entrances[i] and region.covers(Point(point)):
                        # Check the short bridge from the usage envelope. Its
                        # own envelope is intentionally adjacent to this bridge.
                        if clear_bridge(anchor, point, width, radius, shell, obstacle_index, len(static) + position - 1):
                            matches.append((i, point, dist(anchor, point)))
            if not matches:
                unreachable.append(position)
                continue
            access_count += 1
            # Store all permitted approaches so travel can choose the best.
            for i, point, tail in matches:
                targets[i].append((access_count, point, tail))

    result = [check("exit_access", "fail" if blocked_exits else "pass",
                    f"检查全部 {len(project.exits)} 个已确认出口的开口和室内通行连接",
                    blocked=blocked_exits),
              check("continuous_access", "fail" if unreachable else "pass",
                    f"按项目净宽 {width} mm 检查工位及功能使用区边缘至出口的通行",
                    disconnected=sorted(set(unreachable)), reachable_accesses=access_count)]
    if project.req["required_approaches"]:
        approach_status = []
        for name, (point, entry) in project.req["required_approaches"].items():
            accessible = any(region.covers(point) and (entry in entrance_names[i] if entry else entrance_names[i])
                             for i, region in enumerate(regions))
            approach_status.append({"name": name, "entry": entry, "reachable": accessible})
        blocked = [{"name": target["name"], "entry": target["entry"]}
                   for target in approach_status if not target["reachable"]]
        result.append(check("required_approaches", "fail" if blocked else "pass",
                            f"按 {width} mm 核对指定入口至门前检测点",
                            targets=approach_status, disconnected=blocked))
    limit = project.req.get("max_travel_mm")
    if limit is None:
        result.append(check("travel_distance", "pending", "项目未提供路径长度上限，留待专项核对"))
    elif blocked_exits or unreachable:
        result.append(check("travel_distance", "fail", "通行连接存在阻断，无法验证项目路径长度"))
    else:
        shortest = {}
        for i, entries in targets.items():
            if entries:
                lengths = route_distances(regions[i], entrances[i], [(p, tail) for _, p, tail in entries])
                for (key, _, _), length in zip(entries, lengths):
                    if length is not None:
                        shortest[key] = min(length, shortest.get(key, float("inf")))
        worst = max(shortest.values(), default=0)
        passed = len(shortest) == access_count and ceil(worst) <= limit
        result.append(check("travel_distance", "pass" if passed else "fail",
                            "核对使用区边缘至已确认出口的二维路径长度上界",
                            upper_bound_mm=ceil(worst) if len(shortest) == access_count else None,
                            project_limit_mm=limit))
    return result


def recommendation_metrics(project, placements, audit):
    width = project.req["aisle_mm"]
    radius = width / (2 * cos(pi / 32)) + 1
    boxes = [footprint(p, project) for p in placements]
    regions, _ = walking_regions(project, boxes, radius)
    entries = [o.point(o.throat_mm + radius + 15) for o in project.exits]
    accessible = sum(r.area for r in regions if any(r.covers(p) for p in entries)) / 1e6
    door_centers = [o.line.centroid for o in project.exits]
    visitor_names = project.req["visitor_entries"]
    shell = project.outline.buffer(.5)
    visitor_regions = [(o.line.centroid, region) for o, probe in zip(project.exits, entries)
                       if not visitor_names or o.name in visitor_names
                       for region in regions if region.covers(probe)]
    visitor_distances, visitor_unconnected = [], 0
    fixed = [shape for _, _, shape in walking_obstacles(project)]
    obstacle_index = shapely.STRtree(fixed + boxes)
    for i, (p, b) in enumerate(zip(placements, boxes)):
        use = use_of(p["type"], project)
        if use not in ("meeting", "lounge", "display", "counter", "dining", "gallery"):
            continue
        approaches = ports(p, radius, project)
        if use == "counter":
            approaches = approaches[:1]
        distances = [b.centroid.distance(door) for anchor, point in approaches
                     if clear_bridge(anchor, point, width, radius, shell, obstacle_index, len(fixed) + i)
                     for door, region in visitor_regions if region.covers(Point(point))]
        if distances:
            visitor_distances.append(min(distances))
        else:
            visitor_unconnected += 1
    objects = pieces(placements, project)
    private = [shapely.Polygon(p["polygon"]).centroid for p in objects if p["kind"] in ("desk", "bed")]
    obstacles = [shape for _, kind, shape in project.fixed if kind in ("wall", "column")]
    obstacles += [shapely.Polygon(p["polygon"]) for p in objects
                  if p["kind"] == "screen" and p["base"] == 0 and p["height"] >= 1500]
    screen = unary_union(obstacles)

    def average_distance(destinations, sources):
        return round(sum(min(d.distance(o) for o in sources) for d in destinations) / len(destinations) / 1000, 2)

    def screened(door, target):
        ray = LineString([door, target])
        return not shell.covers(ray) or ray.intersects(screen)

    hidden = sum(all(screened(door, p) for door in door_centers) for p in private)
    metrics = {
        "connected_clear_area_m2": round(accessible, 2),
        "quoted_cents": None if audit["missing_prices"] else audit["subtotal_cents"],
        "private_screened_percent": round(100 * hidden / len(private)) if private else None,
        "private_target_count": len(private),
        "private_straight_line_m": average_distance(private, door_centers) if private else None,
        "visitor_straight_line_m": round(sum(visitor_distances) / len(visitor_distances) / 1000, 2)
        if visitor_distances and not visitor_unconnected else None,
        "visitor_unconnected_groups": visitor_unconnected,
    }
    uses = [use_of(p["type"], project) for p in placements]
    pods = [b for use, b in zip(uses, boxes) if use == "focus_pod"]
    active = [b for use, b in zip(uses, boxes) if use in ("meeting", "workshop", "gallery", "lounge")]
    if pods and active:
        metrics["focus_separation_m"] = round(min(pod.distance(area) for pod in pods for area in active) / 1000, 2)
    return metrics


def rank_options(project, options):
    priorities = project.req["priorities"] or ["comfort"]
    ranked = [{"id": o["id"], "strategy": o["strategy"],
               "metrics": recommendation_metrics(project, o["placements"], o["audit"])}
              for o in options if o["audit"]["passed"]]
    key_for = {"comfort": "connected_clear_area_m2", "cost": "quoted_cents",
               "privacy": "private_screened_percent", "visitor": "visitor_straight_line_m",
               "focus": "focus_separation_m"}
    applied = [p for p in priorities if ranked and
               (any(o["metrics"][key_for[p]] is not None for o in ranked) if p == "visitor"
                else all(o["metrics"].get(key_for[p]) is not None for o in ranked))]
    unavailable = {"cost": "有方案缺价，暂无法公平比较",
                   "visitor": "缺少已连通指定入口的访客功能区",
                   "privacy": "缺少可比较的床位或桌面",
                   "focus": "需同时有专注舱和会议、工作台、展陈或休息区"}
    skipped = {p: unavailable.get(p, "缺少可比较的功能区域") for p in priorities if p not in applied}

    def score(row):
        metrics = row["metrics"]
        result = []
        for p in applied:
            value = metrics[key_for[p]]
            if p == "visitor":
                result.extend([metrics["visitor_unconnected_groups"], value if value is not None else float("inf")])
            else:
                result.append(value if p == "cost" else -value)
            if p == "privacy":
                result.append(-metrics["private_straight_line_m"])
        return tuple(result)

    ranked.sort(key=score)
    tied = [row["id"] for row in ranked if applied and score(row) == score(ranked[0])]
    last_score, rank = None, None
    for index, row in enumerate(ranked, 1):
        current = score(row)
        if current != last_score:
            rank = index
            last_score = current
        row["rank"] = rank if applied else None
        m = row["metrics"]
        descriptions = {
            "comfort": f"连通通行中心区域 {m['connected_clear_area_m2']:g} m²",
            "cost": f"量化范围已报价 {((m['quoted_cents'] or 0) / 100):,.2f} 元",
            "privacy": f"{m['private_target_count']} 个床位或桌面采样点中，二维入口视线受遮挡 {m['private_screened_percent']}%，至入口平均直线距离 {m['private_straight_line_m']} m",
            "visitor": (f"{m['visitor_unconnected_groups']} 个访客功能区未连通指定入口"
                        if m["visitor_unconnected_groups"] else
                        f"访客区至最近可达{'指定访客入口' if project.req['visitor_entries'] else '已确认出口'}的平均直线距离 {m['visitor_straight_line_m']} m"),
            "focus": f"专注舱至最近活跃区的平面净距 {m.get('focus_separation_m')} m",
        }
        row["reasons"] = [descriptions[p] for p in applied]
    return {"recommended_option": ranked[0]["id"] if ranked and applied else None,
            "priorities": priorities, "applied_priorities": applied, "skipped_priorities": skipped,
            "ranking": ranked, "tied_first_options": tied, "visitor_entries": project.req["visitor_entries"],
            "basis": "按用户优先顺序比较本次已验证候选。连通中心区域、平面视线和直线距离为设计比较指标。"}


def quantities(project, placements):
    custom = project.req["furniture"]
    counts = Counter(p["kind"] for p in pieces([p for p in placements if p["type"] not in custom], project))
    counts.update(p["type"] for p in placements if p["type"] in custom)
    structural = unary_union([shape for _, kind, shape in project.fixed if kind in {"column", "wall", "service"}])
    net = project.outline.difference(structural).area / 1_000_000
    counts["flooring_m2"] = Decimal(str(net)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    items = []
    for key, qty in sorted(counts.items()):
        spec = custom.get(key)
        unit = spec["unit_cents"] if spec else project.prices.get(key)
        amount = None if unit is None else int((Decimal(qty) * unit).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        name = spec["name"] if spec else NAMES[key]
        if spec:
            name += f"（{spec['width_mm']}×{spec['depth_mm']} mm）"
        items.append({"key": key, "name": name, "quantity": str(qty),
                      "unit": "m²" if key == "flooring_m2" else "组" if spec and spec["kind"] == "dining" else "件",
                      "unit_cents": unit, "amount_cents": amount})
    return items


def validate(project, placements):
    required = project.req
    if not isinstance(placements, list) or len(placements) > 240:
        raise InputError("方案摆放记录格式错误")
    for place in placements:
        if (not isinstance(place, dict) or set(place) != {"type", "x", "y", "angle"}
                or (place["type"] not in TEMPLATES and place["type"] not in required["furniture"])
                or type(place["angle"]) is not int
                or place["angle"] not in (0, 90, 180, 270)
                or any(type(place[k]) not in (int, float) or not -1e8 <= place[k] <= 1e8 for k in ("x", "y"))):
            raise InputError("方案家具坐标或类型无效")
    occupied = [footprint(p, project) for p in placements]
    obstacles = [shape for _, _, shape in project.fixed + project.clear + walking_fittings(project)]
    outside = [i for i, shape in enumerate(occupied, 1) if not project.outline.covers(shape)]
    void_conflicts = [{"item": i, "void": name} for i in outside for name, hole in project.voids
                      if occupied[i - 1].intersection(hole).area > .01]
    intersecting = shapely.STRtree(occupied).query(occupied, predicate="intersects").T
    overlap = sorted((int(i) + 1, int(j) + 1) for i, j in intersecting
                     if i < j and occupied[i].intersection(occupied[j]).area > .01)
    fixed_collisions = [i for i, shape in enumerate(occupied, 1)
                        if any(shape.intersection(g).area > .01 for g in obstacles)]
    wrong_zone = [i for i, (placement, shape) in enumerate(zip(placements, occupied), 1)
                  if (use_of(placement["type"], project) in required["zones"]
                      and not required["zones"][use_of(placement["type"], project)].covers(shape))]
    actual_desks = sum(template(p["type"], project).desks for p in placements)
    actual_meetings = sorted(int(p["type"][7]) for p in placements if p["type"].startswith("meeting"))
    count = Counter(p["type"] for p in placements)
    programs = (actual_desks == required["desks"] and actual_meetings == sorted(required["meeting_seats"])
                and all(count[k] == required[k] for k in ("pantry", "storage", "lounge"))
                and all(count[key] == spec["count"] for key, spec in required["furniture"].items()))
    checks = [check("program", "pass" if programs else "fail", "核对工位、会议座位及各项配套数量",
                    desks=actual_desks, meeting_seats=actual_meetings),
              check("containment", "fail" if outside else "pass", "家具及使用空间位于确认边界内", items=outside),
              check("furniture_collision", "fail" if overlap else "pass", "各家具使用空间互不重叠", items=overlap),
              check("fixed_and_doors", "fail" if fixed_collisions else "pass", "固定设施和门扇禁占区保持净空",
                    items=fixed_collisions),
              check("assigned_zones", "fail" if wrong_zone else "pass", "已指定的功能区满足摆放边界",
                    items=wrong_zone)]
    if void_conflicts:
        checks[1]["void_conflicts"] = void_conflicts
    if required["clear_routes"]:
        checks.append(protected_route_check(project, occupied))
    if required["work_groups"]:
        desk_groups = [(shape, template(p["type"], project).desks) for p, shape in zip(placements, occupied)]
        counts = [{"name": name, "required": group["desks"],
                   "placed": sum(n for shape, n in desk_groups if n and group["polygon"].covers(shape))}
                  for name, group in required["work_groups"].items()]
        checks.append(check("work_groups", "pass" if all(row["placed"] == row["required"] for row in counts) else "fail",
                            "按团队分区核对工位数量与完整使用范围", groups=counts))
    locked = locked_placements(project)
    if locked:
        moved = [required["furniture"][p["type"]]["name"] for p in locked if p not in placements]
        checks.append(check("locked_placements", "fail" if moved else "pass", "核对指定家具坐标和朝向", moved=moved))
    if not any(row["status"] == "fail" for row in checks):
        checks.extend(circulation(project, occupied, placements))
    if required["furniture"]:
        checks[0]["furniture"] = {key: count[key] for key in required["furniture"]}
    all_pieces = pieces(placements, project)
    if required["interior"]:
        conflicts = design_conflicts(project, all_pieces)
        checks.append(check("interior_geometry", "fail" if conflicts else "pass",
                            "室内设计元素与家具实体及人视机位核对", conflicts=conflicts))
        checks.append(check("interior_installation", "pending", "饰面选样、墙面基层、吊装节点、用电与消防机电深化待核"))
    if project.height is None:
        checks.append(check("ceiling", "pending", "层高待确认，三维视图的竖向轮廓为示意"))
    else:
        height = max((p["base"] + p["height"] for p in all_pieces), default=0)
        checks.append(check("ceiling", "pass" if height <= project.height else "fail", "核对家具最高点与已确认层高"))
    checks.append(check("fire_and_structure", "pending",
                        "建筑类别、防火分区、疏散容量、无障碍、机电及结构实施由项目专业复核"))
    items = quantities(project, placements)
    subtotal = sum(row["amount_cents"] for row in items if row["amount_cents"] is not None)
    missing = [row["name"] for row in items if row["amount_cents"] is None]
    cap = required["budget_cents"]
    if cap is None:
        budget_status = "pending"
    elif subtotal > cap:
        budget_status = "fail" if required["budget_mode"] == "cap" else "warning"
    elif missing:
        budget_status = "fail" if required["budget_mode"] == "cap" else "pending"
    else:
        budget_status = "pass"
    checks.append(check("budget", budget_status, "仅对量化范围核对参考预算",
                        priced_subtotal_cents=subtotal, missing_prices=missing, limit_cents=cap))
    return {"checks": checks, "items": items, "subtotal_cents": subtotal, "missing_prices": missing,
            "desks": actual_desks, "passed": not any(row["status"] == "fail" for row in checks)}


def inventory(req, strategy, variant):
    result = []
    for seats in sorted(req["meeting_seats"], reverse=True):
        result.append(f"meeting{seats}" + ("s" if strategy == "focus" else ""))
    for name in ("pantry", "storage", "lounge"):
        result.extend([name] * req[name])
    def envelope_area(item):
        _, spec = item
        if spec["kind"] not in ("workshop", "focus_pod", "planter", "gallery"):
            return spec["width_mm"] * (spec["depth_mm"] + spec["access_mm"])
        t = furniture_template(spec["kind"], spec["width_mm"], spec["depth_mm"], spec["height_mm"],
                               spec["access_mm"], spec["access_sides"])
        return t.width * t.depth

    for key, spec in sorted(req["furniture"].items(), key=envelope_area, reverse=True):
        result.extend([key] * spec["count"])
    jobs = [(token, None) for token in result]
    if req["work_groups"]:
        for name, group in req["work_groups"].items():
            jobs.extend((token, name) for token in desk_inventory(group["desks"], strategy, variant))
    else:
        jobs.extend((token, None) for token in desk_inventory(req["desks"], strategy, variant))
    if variant in (2, 5, 7):
        jobs.sort(key=lambda job: not bool(job[0] in TEMPLATES and TEMPLATES[job[0]].desks))
    return jobs


def desk_inventory(count, strategy, variant):
    size = 4 if strategy == "open" else 2
    if variant in (4, 7):
        size = 2 if size == 4 else 1
    if strategy == "focus":
        return ["focus2"] * (count // 2) + ["focus1"] * (count % 2) if size == 2 else ["focus1"] * count
    grouped, remainder = divmod(count, size)
    return [f"desk{size}" if size > 1 else "desk"] * grouped + ["desk2"] * (remainder // 2) + ["desk"] * (remainder % 2)


def make_anchors(project, template_key, domain, access_domain, radius, region=None):
    t = template(template_key, project)
    assigned = project.req["zones"].get(use_of(template_key, project))
    if region is not None:
        assigned = region if assigned is None else assigned.intersection(region)
    if assigned is not None:
        domain = domain.intersection(assigned)
        prepare(domain)
    scope = region if region is not None else project.outline
    x0, y0, x1, y1 = scope.bounds
    step = 350 if scope.area < 350_000_000 else 500
    entries = []
    for angle in (0, 90, 180, 270):
        w, d = (t.width, t.depth) if angle % 180 == 0 else (t.depth, t.width)
        xs = list(range(ceil(x0 + 12), floor(x1 - w - 11) + 1, step))
        ys = list(range(ceil(y0 + 12), floor(y1 - d - 11) + 1, step))
        if xs and x1 - w - 12 > xs[-1]:
            xs.append(floor(x1 - w - 12))
        if ys and y1 - d - 12 > ys[-1]:
            ys.append(floor(y1 - d - 12))
        if not xs or not ys:
            continue
        xx, yy = (v.ravel() for v in np.meshgrid(xs, ys, indexing="ij"))
        usable = shapely.covers(domain, shapely.box(xx, yy, xx + w, yy + d))
        access = []
        for x, y, dx, dy in t.ports:
            px, py = rotated(x + dx * (radius + 15), y + dy * (radius + 15),
                             t.width, t.depth, angle)
            access.append(shapely.covers(access_domain, shapely.points(xx + px, yy + py)))
        separate = t.desks or template_key.startswith("meeting") or template_key in project.req["furniture"]
        usable &= np.logical_and.reduce(access) if separate else np.logical_or.reduce(access)
        entries.extend((int(x), int(y), int(x+w), int(y+d), angle) for x, y in zip(xx[usable], yy[usable]))
    return entries


def search(project, *, selected=None, previous=None):
    if any(o.line.length < project.req["exit_min_mm"] for o in project.exits):
        return [], {"attempts": 0, "reason": "已确认出口窄于本次配置的出口最小净宽，请核对开口与项目宽度要求"}
    locked = locked_placements(project)
    locked_areas = [footprint(p, project) for p in locked]
    preflight = []
    if locked:
        base_checks = {"containment", "furniture_collision", "fixed_and_doors", "assigned_zones", "protected_routes", "ceiling",
                       "interior_geometry"}
        preflight.extend(c for c in validate(project, locked)["checks"] if c["id"] in base_checks)
    elif project.req["clear_routes"]:
        preflight.append(protected_route_check(project, []))
    if project.req["required_approaches"]:
        preflight.extend(c for c in circulation(project, locked_areas, locked)
                         if c["id"] in ("required_approaches", "exit_access"))
    failures = {c["id"]: c for c in preflight if c["status"] == "fail"}
    if failures:
        reasons = []
        for name, row in failures.items():
            if name == "protected_routes":
                for issue in row["conflicts"]:
                    obstacles = "、".join(issue["fixed"])
                    detail = "越出轮廓" if issue["outside"] else f"与{obstacles}冲突" if obstacles else "被固定放置的家具占用"
                    reasons.append(f"{issue['name']}（{issue['width_mm']} mm）{detail}")
            elif name == "required_approaches":
                reasons.extend(f"{t['name']}无法从{t['entry'] or '已确认入口'}到达"
                               for t in row["disconnected"])
            elif name == "containment" and row.get("void_conflicts"):
                reasons.extend(f"{project.req['furniture'][locked[issue['item'] - 1]['type']]['name']}的使用范围进入{issue['void']}"
                               for issue in row["void_conflicts"])
            else:
                reasons.append(row["description"] + "未通过")
        reason = "。".join(reasons)
        return [], {"attempts": 0, "reason": reason, "rejected": dict.fromkeys(failures, 1),
                    "examples_of_rejections": failures}

    radius = project.req["aisle_mm"] / (2 * cos(pi / 32)) + 1
    blocked = [p for _, _, p in project.fixed + project.clear + walking_fittings(project)]
    blocked.extend(o.vestibule(o.throat_mm + project.req["aisle_mm"] * 1.2) for o in project.exits)
    blocked.extend(shape for _, _, shape in project.req["clear_routes"].values())
    blocked.extend(point.buffer(radius + 2) for point, _ in project.req["required_approaches"].values())
    domain = project.outline.difference(unary_union(blocked).buffer(2))
    prepare(domain)
    access_domain = project.outline.buffer(-radius).difference(
        unary_union([shape for _, _, shape in walking_obstacles(project)]).buffer(radius))
    prepare(access_domain)
    center = project.outline.centroid
    visitor_names = project.req["visitor_entries"]
    entrances = [o for o in project.exits if not visitor_names or o.name in visitor_names]
    area_bounds = {name: row["polygon"].bounds for name, row in project.req["work_groups"].items()}
    area_bounds[None] = project.outline.bounds
    area_centers = {name: row["polygon"].centroid for name, row in project.req["work_groups"].items()}
    gap = project.req["aisle_mm"] + 32
    anchors, candidates, failures, examples = {}, [], Counter(), {}
    attempts = 0
    for strategy in ([selected] if selected else STRATEGIES):
        for variant in range(8):
            ex, ey = entrances[variant % len(entrances)].line.centroid.coords[0]
            attempts += 1
            used = [area.bounds for area in locked_areas]
            placements = list(locked)
            ordered = {}
            for token, group in inventory(project.req, strategy, variant):
                if "placement" in project.req["furniture"].get(token, {}):
                    continue
                key = (token, group)
                if key not in anchors:
                    region = project.req["work_groups"][group]["polygon"] if group else None
                    anchors[key] = make_anchors(project, token, domain, access_domain, radius, region)
                is_work = bool(template(token, project).desks)
                use = use_of(token, project)
                x0, y0, x1, y1 = area_bounds[group]
                nx, ny = [(x0, y1), (x1, y1), (x1, y0), (x0, y0)][variant % 4]
                if is_work:
                    if strategy == "open":
                        work_center = area_centers.get(group, center)
                        tx, ty = .55 * nx + .45 * work_center.x, .55 * ny + .45 * work_center.y
                    else:
                        tx, ty = nx, ny
                elif use == "gallery":
                    tx, ty = (ex, ey) if strategy != "focus" or variant % 2 == 0 else (nx, ny)
                elif use in ("meeting", "lounge", "display", "counter", "dining"):
                    tx, ty = (ex, ey) if strategy == "open" or variant % 2 else (nx, ny)
                elif use == "focus_pod" or (use == "bed" and strategy == "focus"):
                    tx, ty = max(((x0, y0), (x0, y1), (x1, y0), (x1, y1)),
                                 key=lambda pos: dist(pos, (ex, ey)))
                elif use == "workshop":
                    tx, ty = (.65 * center.x + .35 * nx, .65 * center.y + .35 * ny)
                    if strategy == "focus":
                        tx, ty = (.65 * center.x + .35 * ex, .65 * center.y + .35 * ey)
                elif use == "planter" and strategy != "focus":
                    tx, ty = (.7 * nx + .3 * center.x, .7 * ny + .3 * center.y)
                else:
                    tx, ty = nx, y0 if variant % 2 else y1
                if previous and is_work:
                    old = [p for p in previous if p["type"] in TEMPLATES and TEMPLATES[p["type"]].desks]
                    if old:
                        tx, ty = old[len(used) % len(old)]["x"], old[len(used) % len(old)]["y"]

                def merit(entry):
                    a, b, c, d, angle = entry
                    dx, dy = abs((a + c) / 2 - tx), abs((b + d) / 2 - ty)
                    orient = 0 if angle % 180 == (90 if variant in (1, 3, 5) else 0) else 270
                    if variant in (1, 3, 6):
                        dx *= 1.3
                    return dx + dy + orient

                if previous and is_work:
                    entries = sorted(anchors[key], key=merit)
                else:
                    if key not in ordered:
                        ordered[key] = sorted(anchors[key], key=merit)
                    entries = ordered[key]
                found = None
                for a, b, c, d, angle in entries:
                    if all(c + gap <= u or w + gap <= a or d + gap <= v or z + gap <= b
                           for u, v, w, z in used):
                        found = (a, b, c, d, angle)
                        break
                if found is None:
                    failures["packing"] += 1
                    break
                a, b, c, d, angle = found
                used.append((a, b, c, d))
                placements.append({"type": token, "x": a, "y": b, "angle": angle})
            else:
                audit = validate(project, placements)
                if audit["passed"]:
                    distinguishing = [p for p in placements
                                      if project.req["furniture"] or template(p["type"], project).desks]
                    desks = unary_union([footprint(p, project) for p in distinguishing or placements])
                    candidates.append((strategy, placements, audit, desks))
                else:
                    for rule in audit["checks"]:
                        if rule["status"] == "fail":
                            failures[rule["id"]] += 1
                            examples.setdefault(rule["id"], rule)
    chosen, seen = [], set()
    if candidates and project.req["priorities"]:
        by_score = rank_options(project, [
            {"id": i, "strategy": row[0], "placements": row[1], "audit": row[2]}
            for i, row in enumerate(candidates)])
        candidates = [candidates[row["id"]] for row in by_score["ranking"]]
    # Prefer one candidate per strategy, then use distinct variants when
    # a strategy cannot fit. Occupied desk areas must actually differ.
    def consider(row):
        strategy, placements, audit, desks = row
        signature = tuple(sorted((p["type"], p["x"], p["y"], p["angle"]) for p in placements))
        if signature in seen:
            return False
        if any(desks.intersection(other).area / desks.union(other).area > .78 for _, _, _, other in chosen):
            return False
        seen.add(signature)
        chosen.append(row)
        return True

    wanted = 1 if selected else project.req["options"]
    strategy_order = dict.fromkeys(row[0] for row in candidates) if project.req["priorities"] else STRATEGIES
    for strategy in ([selected] if selected else strategy_order):
        for row in candidates:
            if row[0] == strategy and consider(row):
                break
        if len(chosen) == wanted:
            break
    for row in candidates:
        if len(chosen) == wanted:
            break
        consider(row)
    return [(s, p, a) for s, p, a, _ in chosen], {
        "attempts": attempts, "validated_candidates": len(candidates),
        "rejected": dict(failures), "examples_of_rejections": examples,
    }
