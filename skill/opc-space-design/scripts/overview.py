"""Whole-space object and function index, built from the selected layout."""

from math import radians

from shapely.geometry import LineString, Point, Polygon

from layout import pieces, template
from model import InputError, as_ring, label, object_fields, polygon
from procurement import MATERIAL_NAMES, furniture_name
from visual import scene


def indexed_objects(project, placements):
    items = []

    def add(prefix, index, name, kind, category, shape=None, segment=None,
            dimensions=None, bottom=None, top=None, note=None, **extra):
        items.append({
            "id": f"{prefix}{index:03d}", "name": name, "kind": kind, "category": category,
            "state": {"furniture": "拟布置", "interior": "拟装修"}.get(category, "原状保留"),
            "polygon": as_ring(shape) if shape is not None else None,
            "segment": [list(xy) for xy in segment] if segment is not None else None,
            "dimensions_mm": dimensions or {}, "bottom_mm": bottom, "top_mm": top,
            "zone": None, "note": note, **extra,
        })

    count = 0
    for group, placement in enumerate(placements, 1):
        t = template(placement["type"], project)
        spec = project.req["furniture"].get(placement["type"])
        for index, (part, body) in enumerate(zip(t.pieces, pieces([placement], project), strict=True), 1):
            kind, _, _, width, depth, height, bottom = part
            count += 1
            add("F", count, furniture_name(kind, spec), kind, "furniture", Polygon(body["polygon"]),
                dimensions={"width_mm": width, "depth_mm": depth, "height_mm": height},
                bottom=bottom, top=bottom + height, placement_index=group, part_index=index,
                angle_deg=placement["angle"], color=body.get("color_hex"))
    design = project.req["interior"]
    for index, element in enumerate(design["elements"] if design else [], 1):
        kind, shape, segment = (element[key] for key in ("kind", "footprint", "segment"))
        bottom, top = (element[key] for key in ("bottom_mm", "top_mm"))
        dimensions = {}
        if segment:
            span = LineString(segment).length
            dimensions = {"width_mm": round(span, 3), "depth_mm": round(shape.area / span, 3),
                          "height_mm": round(top - bottom, 3)}
        elif kind != "floor_finish":
            x0, y0, x1, y1 = shape.bounds
            dimensions = {"width_mm": round(x1 - x0, 3), "depth_mm": round(y1 - y0, 3),
                          "height_mm": round(top - bottom, 3)}
        name = element["material_name"] or MATERIAL_NAMES.get(element["material"], element["material"])
        note = f"拟用{name}，范围 {shape.area / 1e6:.2f} m²。" if kind == "floor_finish" else f"拟用{name}。"
        add("D", index, element["name"], kind, "interior", shape, segment, dimensions, bottom, top, note,
            color=element["color"])
    for index, (name, kind, shape) in enumerate(project.fixed, 1):
        x0, y0, x1, y1 = shape.bounds
        add("S", index, name, kind, "fixed", shape,
            dimensions={"width_mm": round(x1 - x0, 3), "depth_mm": round(y1 - y0, 3)},
            note="按底图保留平面范围，内部设备和竖向细节依现场确认。")
    for index, opening in enumerate(project.exits + project.windows, 1):
        kind = "exit" if index <= len(project.exits) else "window"
        dims = {"span_mm": round(opening.line.length, 3)}
        bottom = 0 if kind == "exit" else opening.sill_mm
        if opening.head_mm is not None and bottom is not None:
            dims["height_mm"] = opening.head_mm - bottom
        add("O", index, opening.name, kind, "opening", segment=opening.line.coords,
            dimensions=dims, bottom=bottom, top=opening.head_mm)
    for index, (name, shape) in enumerate(project.voids, 1):
        add("V", index, name, "void", "void", shape, note=f"楼板洞口 {shape.area / 1e6:.2f} m²。")
    return items


def default_needs(project, items):
    def row(name, kinds, note):
        refs = [item["id"] for item in items if item["kind"] in kinds]
        return {"name": name, "refs": refs, "note": note}

    result = [row("进入与通行", {"exit"}, "底图开口及通行以本项目校核条件为准。")]
    if project.req["desks"]:
        result.extend([
            row("日常办公", {"desk", "office_chair"}, "工位和取用空间按已确认人数布置。"),
            row("接待与讨论", {"lounge", "sofa", "gallery", "display", "meeting_table_4",
                            "meeting_table_6", "meeting_table_8"}, "根据访客频率确认共用或独立使用。"),
            row("茶水与补给", {"pantry", "water_station"}, "有配套时核对取用、电源和维护条件。"),
            row("物品收纳", {"storage", "entry_organizer", "wardrobe"}, "确认公用物品和个人物品的收纳方式。"),
        ])
    elif any(item["kind"] == "bed" for item in items):
        result.extend([
            row("起居", {"sofa", "coffee_table"}, "核对常住人数及使用家具。"),
            row("就餐", {"dining", "dining_chair"}, "核对椅子拉开和家务通行。"),
            row("睡眠", {"bed"}, "核对床边使用空间。"),
            row("衣物与日常收纳", {"wardrobe", "storage", "entry_organizer"}, "按居住者物品和开门动作确定柜型。"),
            {"name": "厨卫", "refs": [i["id"] for i in items if i["category"] == "fixed"
                                   and any(word in i["name"] for word in ("厨房", "厨卫", "卫生间"))],
             "note": "先核对底图厨卫范围，内部设备按现场明细设计。"},
        ])
    elif any(item["kind"] == "counter" for item in items):
        result.extend([
            row("接待与结算", {"counter"}, "核对服务面与员工工作面。"),
            row("浏览与展示", {"display", "gallery", "display_wall"}, "留出驻足与更换展品空间。"),
            row("后场补货", {"storage"}, "确认存放与补货路线。"),
        ])
    return result


def brief_details(project, layout, items, brief):
    supplied = brief.get("layout_sha256")
    if supplied is not None and supplied != layout["layout_sha256"]:
        raise InputError("整区说明中的布局版本与本方案不一致")
    source = brief.get("zones")
    if source is None:
        source = [{"name": name, "polygon": as_ring(group["polygon"])}
                  for name, group in project.req["work_groups"].items()]
    if not isinstance(source, list) or len(source) > 32:
        raise InputError("整区分区须为不超过 32 项的列表")
    zones = []
    for row in source:
        object_fields(row, "name polygon note", "整区分区")
        name = label(row.get("name"), "分区名称")
        shape = polygon(row.get("polygon"), name)
        if not project.outline.covers(shape):
            raise InputError(f"整区分区 {name} 越出可用楼板或覆盖洞口")
        if any(name == old["name"] or shape.intersection(old["shape"]).area > .01 for old in zones):
            raise InputError(f"整区分区 {name} 与其他分区重名或重叠")
        note = label(row["note"], f"{name}说明") if row.get("note") else ""
        zones.append({"id": f"Z{len(zones)+1:02d}", "name": name, "polygon": as_ring(shape), "note": note, "shape": shape})
    x0, y0, _, _ = project.outline.bounds
    for item in items:
        if item["polygon"] is not None:
            shape = Polygon(item["polygon"])
            anchor = shape.representative_point()
            reference = shape.bounds[:2]
            basis = "本体平面外接范围左下"
        elif item["segment"] is not None:
            anchor = LineString(item["segment"]).centroid
            reference = item["segment"][0]
            basis = "线段起点"
        else:
            continue
        item["zone"] = next((zone["name"] for zone in zones if zone["shape"].covers(anchor)), None)
        item["position_mm"] = {"x_mm": round(reference[0] - x0, 3), "y_mm": round(reference[1] - y0, 3),
                               "basis": basis}
    by_id = {item["id"]: item for item in items}
    names = set()
    entries = []
    raw_needs = brief.get("needs", default_needs(project, items))
    if not isinstance(raw_needs, list) or len(raw_needs) > 32:
        raise InputError("整区功能须为不超过 32 项的列表")
    for row in raw_needs:
        object_fields(row, "name refs note", "整区功能")
        name = label(row.get("name"), "功能名称")
        if name in names:
            raise InputError(f"整区功能名称重复：{name}")
        names.add(name)
        refs = row.get("refs", [])
        if not isinstance(refs, list) or any(not isinstance(key, str) or key not in by_id for key in refs):
            raise InputError(f"整区功能 {name} 的物件编号不存在")
        if len(refs) != len(set(refs)):
            raise InputError(f"整区功能 {name} 的物件编号重复")
        note = label(row["note"], f"{name}说明") if row.get("note") else ""
        status = "待确认" if not refs else "底图保留" if all(by_id[key]["state"] == "原状保留" for key in refs) else "已安排"
        entries.append({"name": name, "refs": refs, "note": note, "status": status})
    summary = label(brief.get("summary"), "整区说明", "整区平面、剖切三维与逐件物品使用同一份方案。")
    choices = brief.get("selections", [])
    if not isinstance(choices, list) or len(choices) > 32:
        raise InputError("选型说明须为不超过 32 项的列表")
    selections = []
    choice_names = set()
    for row in choices:
        object_fields(row, "name refs spec note", "选型说明")
        name = label(row.get("name"), "选型名称")
        refs = row.get("refs")
        if (not isinstance(refs, list) or not refs or any(not isinstance(key, str) or key not in by_id for key in refs)
                or len(refs) != len(set(refs))):
            raise InputError(f"选型 {name} 须关联有效且不重复的图上物件编号")
        if name in choice_names:
            raise InputError(f"选型名称重复：{name}")
        choice_names.add(name)
        selections.append({"name": name, "refs": refs, "spec": label(row.get("spec"), f"{name}规格"),
                           "note": label(row["note"], f"{name}说明") if row.get("note") else ""})
    return summary, [{k: v for k, v in zone.items() if k != "shape"} for zone in zones], entries, selections


def overview_data(project, layout, brief=None, asset_root=None, swatches=None, *, with_positions=False):
    brief = object_fields({} if brief is None else brief,
                          "summary zones needs selections layout_sha256 patterns office_services detail_views", "整区说明")
    placements = layout["placements"]
    items = indexed_objects(project, placements)
    summary, zones, needs, selections = brief_details(project, layout, items, brief)
    owners = []
    faces = scene(project, placements, radians(45), owners=owners)
    face_prefix = {"furniture": "F", "interior": "D", "fixed": "S", "window": "O", "exit_header": "O"}
    rendered = []
    for (vertices, color), owner in zip(faces, owners, strict=True):
        item = None
        if owner and owner[0] in face_prefix:
            kind, index = owner
            number = index + 1 + (len(project.exits) if kind == "window" else 0)
            item = f"{face_prefix[kind]}{number:03d}"
        rendered.append({"vertices": [list(point) for point in vertices], "rgb": color, "item": item})
    data = {"title": project.title, "summary": summary, "style": project.req["style"],
            "area_m2": round(project.outline.area / 1e6, 3),
            "aisle_mm": project.req["aisle_mm"], "source": project.raw["geometry"]["source"],
            "outline": {"outer": as_ring(project.outline),
                        "holes": [as_ring(shape) for _, shape in project.voids]},
            "zones": zones, "items": items, "needs": needs, "selections": selections, "faces": rendered,
            "layout_sha256": layout["layout_sha256"]}
    if "patterns" in brief:
        from surface_patterns import surface_patterns
        data["patterns"] = surface_patterns(brief["patterns"], items, asset_root, swatches)
    if "office_services" in brief:
        from office_services import office_services
        data["office_services"] = office_services(project, brief["office_services"], items)
    if "detail_views" in brief:
        from overview_detail import detail_views
        data["detail_views"] = detail_views(brief["detail_views"], items)
    if with_positions:
        from positioning import positioning
        data["positioning"] = positioning(project, placements, items)
    return data
