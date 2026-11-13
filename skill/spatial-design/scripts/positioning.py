"""Site-readable positions for the same pieces used by the plan and axon."""

import csv
from math import dist

from layout import pieces
from model import InputError, as_ring


FRONT_EDGE = {
    "sofa": (0, 1), "bed": (2, 3), "wardrobe": (0, 1), "storage": (0, 1),
    "water_station": (0, 1), "recycling_station": (0, 1), "entry_organizer": (0, 1),
    "waste_bin": (0, 1), "print_station": (0, 1), "cleaning_cabinet": (0, 1),
    "locker_bank": (0, 1), "charging_station": (0, 1), "focus_pod": (0, 1),
    "pantry": (0, 1), "gallery": (0, 1),
}


def millimetres(value):
    return f"{value:.3f}".rstrip("0").rstrip(".")


def positioning(project, placements, items):
    outline = as_ring(project.outline)
    a = min(outline, key=lambda point: (point[1], point[0]))
    b = max(outline, key=lambda point: (dist(a, point), point))
    bodies = iter(pieces(placements, project))
    rows = []
    for item in items:
        if item["category"] not in ("furniture", "interior"):
            continue
        body = next(bodies) if item["category"] == "furniture" else None
        if body and body["kind"] != item["kind"]:
            raise InputError("逐件摆放的编号与家具模型顺序不一致")
        # Template corners carry semantic order even after rotating. Polygon
        # rings also represent other shapes, so wall segments take priority.
        polygon = body["polygon"] if body else item["polygon"]
        points = item["segment"] or polygon
        front = None
        if body and body["kind"] in FRONT_EDGE:
            front = {"label": "床头侧" if body["kind"] == "bed" else "操作或使用侧",
                     "segment": [polygon[i] for i in FRONT_EDGE[body["kind"]]]}
        elif body and "back" in body:
            front = {"label": "椅背侧", "segment": polygon[2:4] if body["back"] == "far" else polygon[:2]}
        rows.append({"ref": item["id"], "name": item["name"], "zone": item["zone"], "front": front,
                     "points_mm": [[round(x - a[0], 3), round(y - a[1], 3)] for x, y in points],
                     "dimensions_mm": item["dimensions_mm"], "angle_deg": item.get("angle_deg"),
                     "bottom_mm": item["bottom_mm"], "top_mm": item["top_mm"]})
    return {"origin_mm": a, "reference_mm": b, "reference_distance_mm": round(dist(a, b), 3), "rows": rows}


def write_positions(path, guide):
    def points(values):
        return " / ".join(f"({millimetres(x)}, {millimetres(y)})" for x, y in values)

    def cell(value):
        return "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value

    fields = {"width_mm": "宽", "depth_mm": "深", "height_mm": "高"}
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["编号", "物件", "分区", "尺寸 mm", "定位点（相对图上 A，X 向右 Y 向上，mm）",
                         "底标高 mm", "顶标高 mm", "旋转 °", "边线说明", "边线点（相对 A，mm）"])
        a = guide["origin_mm"]
        for row in guide["rows"]:
            front = row["front"]
            writer.writerow([row["ref"], cell(row["name"]), cell(row["zone"]),
                             " / ".join(f"{fields[key]} {millimetres(value)}" for key, value in row["dimensions_mm"].items()),
                             points(row["points_mm"]), row["bottom_mm"], row["top_mm"], row["angle_deg"],
                             front["label"] if front else None,
                             points([[round(x - a[0], 3), round(y - a[1], 3)]
                                     for x, y in front["segment"]]) if front else None])
