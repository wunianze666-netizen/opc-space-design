"""Procurement quantities and position references measured from a verified option."""

from collections import Counter
from math import dist

from shapely import union_all
from shapely.geometry import Polygon

from interior import WALL_KINDS
from layout import NAMES, components, footprint, pieces, template
from model import InputError


MATERIAL_NAMES = {"oak": "橡木色", "walnut": "胡桃木色", "sage": "灰绿色", "cobalt": "钴蓝色",
                  "graphite": "石墨灰", "clay": "泥陶色", "ivory": "象牙白", "terrazzo": "水磨石效果",
                  "warm_light": "暖色照明", "cork": "软木色"}
FURNITURE_BUYING = {
    "desk": ("单工位桌，按独立桌面计量。报价含桌架、台面、理线及找平件。",
             "核对桌下净空、排线方向、并桌接缝及桌面走线孔位置。"),
    "office_chair": ("可调办公椅，按带扶手和轮脚的整椅外廓报价。",
                     "确认扶手收桌、轮脚最大外径、座高调节范围及后仰空间。"),
    "meeting_table": ("会议桌按整桌报价，标明拼板、底架、理线和设备安装范围。",
                      "用实际桌脚位置复核四周座位的膝部空间及桌下过线。"),
    "meeting_chair": ("会议椅按单椅报价，标明含扶手时的实际外廓。",
                      "对照桌脚间距、座高和拉椅所需空间。"),
    "dining": ("餐桌为独立主件，按整桌报价，标明台面、桌脚和表面易清洁做法。",
               "核对桌脚间净宽和台面下净高，椅子按清单对应的组件编号配套。"),
    "dining_chair": ("餐椅作为单件计量，供应商提交带脚架的外廓图。",
                     "实配同组餐桌，核对拉出、坐入和椅脚落点。"),
    "workshop": ("双面工作台作为主件，报价拆分台面、下架和可选理线附件。",
                 "确认用途所需载荷和表面耐污性，复核双侧操作净空及与同组白板的间距。"),
    "whiteboard": ("共创白板组件，按板面跨度和含支脚的占地厚度报价。",
                   "核对支脚或底座实际突出量、倾倒防护和板面擦写样板。"),
    "pantry": ("茶水柜按含台面和柜体的外廓报价，台盆、电器及给排水配件列明选配范围。",
               "按现场立管、插座、排水坡度和检修空间复尺，确认门扇和抽屉全开范围。"),
    "storage": ("储物柜按含门板、底脚和把手的整柜外廓报价。",
                "核对门板开合、最大搬运件、层板载荷及防倾固定条件。"),
    "sofa": ("沙发按含扶手、靠背和脚架的成品外廓报价。",
             "核对面料样、组装方式、拆包尺寸和坐面朝向。"),
    "coffee_table": ("茶几按完整底架和台面的成品外廓报价。",
                     "核对边角做法、桌面高度和与沙发的膝部空间。"),
    "bed": ("床架与床垫分别注明供货范围，按安装完成后的最大外廓报价。",
            "确认床头外凸、床垫规格及储物抽屉或床箱开启所需净空。"),
    "focus_pod": ("单人舱按完整舱体报价，分列换气、照明、供电、门扇及装配。",
                  "复核门扇开启、检修和吊装搬运，供应商提交隔声、通风和电气资料供项目确认。"),
    "display": ("展示架按含层板、底座和固定件的成品外廓报价。",
                "确认展品尺寸、层板载荷、防倾条件和实际取放范围。"),
    "gallery": ("展陈装置按主体、可换展示面和固定件分项报价。",
                "确认展品更换路径、挂点、承载及底座与人行空间的关系。"),
    "counter": ("服务台按柜体、台面、面板及所需线槽报价。",
                 "确认站坐工作面、内外侧净空、走线和储物门扇。"),
    "equipment": ("按已布置的设备外廓核价，支座、附件及接线件明确分项。",
                   "复核铭牌、供电、散热、振动、检修面及启用后的最大外廓。"),
    "planter": ("绿植槽按槽体、接水内胆和成品植株总高报价。",
                "确认湿载、承水、维护侧和植物生长后的占地范围。"),
    "potted_plant": ("落地植物按盆器、托盘及成品植株总外廓核对。",
                     "确认光照、浇水维护和叶片展开范围。"),
    "water_station": ("饮水机、杯具和接水部件按整站外廓核对。",
                      "确认取水、换桶或换滤芯的前侧空间和现场电源。"),
    "recycling_station": ("分类回收柜按含开启件的整柜外廓核对。",
                           "确认投放、清运和抽桶方向。"),
    "waste_bin": ("垃圾桶按整件外廓核对，盖子及内桶按使用方式选配。",
                  "按实际开启方向和抽桶空间复核放置、投放及清运。"),
    "print_station": ("设备、承载台和耗材收纳按分项核对。",
                      "确认铭牌、电源、网络、纸盒拉出和检修空间。"),
    "cleaning_cabinet": ("保洁柜按含门板、锁和分隔的整柜外廓核对。",
                         "确认通风、柜门全开、防倾及清洁用品的存放要求。"),
    "locker_bank": ("储物柜按整柜外廓及实际格口数量核对。",
                    "核对开门、防倾和按人数分配的格口。"),
    "charging_station": ("充电站按柜体和电气组件分项核对。",
                         "复核回路、线缆、散热及实际检修要求。"),
    "entry_organizer": ("入口收纳按含挂件、底脚与接水托盘的外廓核对。",
                        "确认挂衣或雨伞放入后仍留出通道。"),
    "wardrobe": ("衣柜按含门板、把手及地脚的外廓核对。",
                 "核对柜门动作、搬运和防倾固定，收纳结构根据使用者确认。"),
}
WALL_BUYING = {
    "slats": "饰面条、背板、端头及阴阳角收边按成套墙面分段报价，提交分格图。",
    "acoustic": "吸声面板、背衬、收边及挂件分项，提交材料和吸声资料供项目复核。",
    "art": "墙艺按外框、饰面及隐藏挂件报价，提交拼版和检修拆装方式。",
    "display_wall": "展示墙按底板、可更换挂轨、面板与收边分项，提交挂点及承载资料。",
    "notice_board": "可更换壁报按板面、边框和挂件列项，确认更换方式。",
}


def length(value):
    return round(float(value), 6)


def area(shape):
    return round(shape.area / 1_000_000, 6)


def xy_list(coordinates):
    return [[float(x), float(y)] for x, y in coordinates]


class Schedule:
    def __init__(self):
        self.items = []
        self.locators = []
        self.by_identity = {}
        self.category_counts = Counter()

    def item(self, category, kind, name, unit, dimensions, spec, check, *, material=None):
        key = (category, kind, name, unit, tuple(dimensions.items()), spec, check, material)
        if key not in self.by_identity:
            row = {"id": f"I{len(self.items) + 1:03d}", "category": category, "kind": kind, "name": name,
                   "quantity": 0, "unit": unit, "dimensions_mm": dimensions,
                   "spec": spec, "preorder_check": check, "locator_ids": []}
            if material is not None:
                row["material"] = material
            self.items.append(row)
            self.by_identity[key] = row
        return self.by_identity[key]

    def locator(self, item, shape, base, top, *, ordered_corners=None, **extra):
        category = item["category"]
        prefix = {"furniture": "F", "wall": "W", "ceiling": "C", "floor": "D"}[category]
        self.category_counts[category] += 1
        key = f"{prefix}{self.category_counts[category]:03d}"
        center = shape.representative_point()
        polygon = ordered_corners if ordered_corners is not None else list(shape.exterior.coords)[:-1]
        row = {"id": key, "item_id": item["id"], "category": category, "kind": item["kind"],
               "name": item["name"], "polygon_mm": xy_list(polygon), "center_mm": [center.x, center.y],
               "base_mm": base, "top_mm": top, **extra}
        if shape.interiors:
            row["holes_mm"] = [xy_list(list(ring.coords)[:-1]) for ring in shape.interiors]
        item["locator_ids"].append(key)
        self.locators.append(row)


def furniture_name(kind, custom):
    if custom is None:
        return NAMES[kind]
    part_labels = {"workshop": "工作台", "whiteboard": "白板", "dining": "餐桌", "dining_chair": "餐椅"}
    return f"{custom['name']}（{part_labels[kind]}）" if kind in part_labels else custom["name"]


def furniture_buying(kind, base):
    if kind == "screen":
        if base > 0:
            return ("桌面屏风按独立板件和夹具报价，跨度与厚度见尺寸。",
                    "核对桌板厚度、夹具突出量和与线槽或桌面接口的干涉。")
        return ("落地屏风按独立板件、支脚及连接件报价。",
                "复核支脚实际占地、稳定性和相邻通道净宽。")
    return FURNITURE_BUYING["meeting_table" if kind.startswith("meeting_table_") else kind]


def furniture_rows(result, project, placements):
    for placement_index, place in enumerate(placements, 1):
        token = place["type"]
        spec = project.req["furniture"].get(token)
        model = template(token, project)
        usage = footprint(place, project)
        team = next((name for name, group in project.req["work_groups"].items()
                     if model.desks and group["polygon"].covers(usage)), None)
        for part, body in zip(model.pieces, pieces([place], project), strict=True):
            kind, _, _, w, d, h, base = part
            dimensions = {"width_mm": w, "depth_mm": d, "height_mm": h, "base_mm": base}
            if kind in {"whiteboard", "screen"}:
                dimensions.update(face_span_mm=max(w, d), thickness_envelope_mm=min(w, d))
            buying, check = furniture_buying(kind, base)
            if spec and kind == spec["kind"]:
                sides = spec.get("access_sides", 2 if kind == "dining" else 1)
                check += f"保留图示 {sides} 侧使用区，各按 {spec['access_mm']} mm 设计值核对。"
            row = result.item("furniture", kind, furniture_name(kind, spec), "件", dimensions, buying, check)
            row["quantity"] += 1
            orientation = {"rotation_deg": place["angle"], "group_label": team, "placement_index": placement_index}
            if "back" in body:
                orientation["back_side"] = body["back"]
            result.locator(row, Polygon(body["polygon"]), base, base + h,
                           ordered_corners=body["polygon"], **orientation)


def ceiling_sizes(shape):
    box = shape.minimum_rotated_rectangle
    points = list(box.exterior.coords)
    sizes = sorted((dist(a, b) for a, b in zip(points, points[1:])))
    rectangle = box.symmetric_difference(shape).area <= max(.01, shape.area * 1e-9)
    return length(sizes[-1]), length(sizes[0]), rectangle


def wall_components(element):
    from visual import wall_finish_geometry

    roles = {
        "slats": (["背板"], "饰面条"),
        "acoustic": (["背衬板"], "吸声面板"),
        "art": (["基板", "底衬", "装饰板", "装饰板", "装饰压条"], "饰面组件"),
        "display_wall": (["背板", "左立边", "右立边"], "展示层板"),
        "notice_board": (["壁报板"], "图面部件"),
    }
    first, repeated = roles[element["kind"]]
    (x0, y0), (x1, y1) = element["segment"]
    span = dist((x0, y0), (x1, y1))
    ux, uy = (x1 - x0) / span, (y1 - y0) / span
    groups = Counter()
    index = 0

    def capture(vertices, bottom, top, color):
        nonlocal index
        along = [(x - x0) * ux + (y - y0) * uy for x, y in vertices]
        across = [(y - y0) * ux - (x - x0) * uy for x, y in vertices]
        name = first[index] if index < len(first) else repeated
        size = (length(max(along) - min(along)), length(max(across) - min(across)), length(top - bottom))
        groups[name, size, color] += 1
        index += 1

    wall_finish_geometry(element, element["color"], capture, None)
    return [{"name": name, "quantity": count, "unit": "件", "model_color_hex": color,
             "dimensions_mm": dict(zip(("span_mm", "depth_mm", "height_mm"), size, strict=True))}
            for (name, size, color), count in groups.items()]


def mounted_rows(result, elements):
    for element in elements:
        kind = element["kind"]
        if kind == "floor_finish":
            continue
        shape = element["footprint"]
        bottom, top = element["bottom_mm"], element["top_mm"]
        dimensions = {"height_mm": length(top - bottom), "bottom_mm": bottom, "top_mm": top}
        material = element["material"]
        finish = f"方案色或效果为{element['material_name'] or MATERIAL_NAMES[material]}，以确认样板为准。"
        segment = element.get("segment")
        if kind in WALL_KINDS:
            category, unit = "wall", "段"
            span = dist(*segment)
            dimensions.update(span_mm=length(span), depth_mm=length(shape.area / span))
            buying = WALL_BUYING[kind]
            check = "按实测墙面长度、基层、开口和检修口复尺，工厂确认分段尺寸、接缝及固定节点。"
        else:
            category = "ceiling"
            long, short, rectangle = ceiling_sizes(shape)
            dimensions.update(length_mm=long, width_mm=short)
            if kind == "ceiling_light" and rectangle and long >= 3 * short:
                dimensions["run_mm"] = long
                unit = "道"
                buying = f"线性灯具按实测深化总长核价，图示单道长 {long:g} mm。厂家按可供长度分段，另列驱动、接头、端盖和吊装件。"
            elif kind == "ceiling_light":
                unit = "处"
                buying = "灯体按图示安装范围报价，长宽为外接范围。分列灯体、驱动和吊装件，灯体构造和分段由厂家施工图确认。"
            else:
                unit = "道" if rectangle else "处"
                buying = "吊顶吸声片按图示单道或异形范围报价，厂家确认成品分段和接缝，分列吊件与端头。"
            check = "现场核对顶标高、可承载固定点、检修与喷淋烟感避让，供电和吊装由项目专业复核。"
        row = result.item(category, kind, element["name"], unit, dimensions, buying + finish, check, material=material)
        row["quantity"] += 1
        if segment:
            row["components"] = wall_components(element)
            row["components_note"] = "每段的可见部件按方案模型拆列，已含于本行。现场复尺后确认生产尺寸、收边和隐蔽挂件。"
        extra = {"segment_mm": xy_list(segment)} if segment else {}
        result.locator(row, shape, bottom, top, **extra)


def floor_rows(result, project, elements):
    from visual import PALETTES

    structural = union_all([shape for _, kind, shape in project.fixed if kind in {"column", "wall", "service"}])
    net = project.outline.difference(structural)
    remaining = net
    patches = [(e["name"], e["material"], e["footprint"]) for e in elements if e["kind"] == "floor_finish"]
    material_names = {**MATERIAL_NAMES, **{e["material"]: e["material_name"] for e in elements if e["material_name"]}}
    for name, material, footprint_ in [*patches, ("其余地面饰面", None, net)]:
        shape = remaining.intersection(footprint_)
        remaining = remaining.difference(footprint_)
        if shape.is_empty or not shape.area:
            continue
        kind = "floor_finish" if material else "floor_remainder"
        if material == "oak":
            spec = "拟选橡木色板材，按图面 1800 × 185 mm 模数试排，报含收口件与辅材的同色样板。"
        elif material == "terrazzo":
            spec = "拟选细骨料水磨石地面，按约 1100 × 1100 mm 的图面分格确认做法及收边。"
        elif material:
            spec = f"按{material_names[material]}样板核价。"
        else:
            pattern = PALETTES[project.req["style"]].get("floor_pattern", "wood")
            spec = {
                "wood": "拟选浅木纹地板，按图面 1620 × 260 mm 模数试排，同一空间确认板色与批次。",
                "light_oak": "拟选浅橡木色地板，按图面 1800 × 185 mm 模数试排。",
                "stone": "拟选浅石材色地面，按约 1100 × 1100 mm 模数试排，确认铺装和收边。",
                "concrete": "拟选哑光混凝土色地面，按图面分区确认基层、色样与成品标高。",
            }[pattern]
        spec += "数量为平面实铺净面积，另核排版损耗、收口和辅材。"
        check = "按完成面标高、基层、拼缝及交界收口现场复尺，核对既有柜底处理与材料供货批次。"
        row = result.item("floor", kind, name, "m²", {}, spec, check, material=material)
        row["quantity"] = area(shape)
        ordered = sorted((part.normalize() for part in components(shape)), key=lambda part: (part.bounds, part.wkt))
        for part in ordered:
            result.locator(row, part, 0, 0, net_area_m2=area(part))
    gross = Polygon(project.outline.exterior.coords)
    patch_union = union_all([shape for _, _, shape in patches]).intersection(net)
    return {"gross_outline_m2": area(gross), "void_m2": area(gross.difference(project.outline)),
            "net_slab_m2": area(project.outline), "fixed_excluded_m2": area(project.outline.intersection(structural)),
            "finish_patches_m2": area(patch_union), "remainder_m2": area(net.difference(patch_union))}


def interfaces(project, items):
    all_rows = [
        {"topic": "现场复尺及运输", "applies_to": "整套方案",
         "check": "核对电梯、门洞、转弯和卸货堆放区。确认超长件是否分段进场、安装顺序和成品保护。"},
        {"topic": "建筑与机电", "applies_to": "整套方案",
         "check": "由项目专业核对疏散、无障碍、喷淋、烟感、送回风和检修口，订货前落实调整责任。"},
    ]
    groups = [
        ("强弱电及数据", {"desk", "meeting_table_4", "meeting_table_6", "meeting_table_8", "pantry", "focus_pod", "equipment", "workshop", "counter", "ceiling_light"},
         "逐处确认电源和网络点位、走线孔、地插方向、灯驱和控制位置，核对接线容量及穿越通道的保护。"),
        ("给排水及检修", {"pantry"}, "对照茶水区与现场立管位置，确认上下水、电器插座、阀件和检修开口。"),
        ("饰面及交界", {"floor_finish", "floor_remainder", *WALL_KINDS}, "确认材质小样、基层、收边色和地墙交界，明确固定柜底是否铺贴及损耗计量。"),
        ("固定及吊装", {*WALL_KINDS, "ceiling_light", "ceiling_baffle", "focus_pod", "display", "gallery", "storage", "whiteboard"},
         "复核基层或楼板可用固定点、成品自重、固定件、吊装与拆换路径，未确认位置时先深化节点。"),
    ]
    for topic, kinds, check in groups:
        matched = [row["id"] for row in items if row["kind"] in kinds]
        if matched:
            all_rows.append({"topic": topic, "applies_to": "、".join(matched), "check": check})
    if project.voids:
        all_rows.append({"topic": "楼板洞口交界", "applies_to": "、".join(name for name, _ in project.voids),
                         "check": "核对洞口边缘成品收口、既有栏护及施工防护，楼板洞口面积已扣减。"})
    if project.req["interior"]:
        all_rows.append({"topic": "基底墙顶与既有设施", "applies_to": "墙顶基层、原门窗、保留设备",
                         "check": "确认保留范围、底色样板、修补和防护。按实测墙面、顶面和现场管线编制施工工程量。"})
    all_rows.extend([
        {"topic": "安装验收", "applies_to": "采购号、安装定位及现场通道",
         "check": "按逐件编号点数，复核落位、固定及成品尺寸。现场试开门和抽屉、拉椅，测量主要通道净宽。"},
        {"topic": "设备调试及移交", "applies_to": "项目采用的照明、空调、网络、设备与给排水",
         "check": "由相关单位完成适用系统调试及验收。保留保修和使用资料，完成清洁及交付确认。"},
    ])
    return all_rows


def schedule(project, layout):
    """Return a JSON-serializable schedule for the selected, verified layout."""
    if not project.ready or ("audit" in layout and not layout["audit"]["passed"]):
        raise InputError("采购清单需要已确认底图和通过校核的布局")
    result = Schedule()
    elements = (project.req["interior"] or {}).get("elements", [])
    furniture_rows(result, project, layout["placements"])
    mounted_rows(result, elements)
    floor = floor_rows(result, project, elements)
    return {"schema": 1, "title": project.title, "layout_id": layout.get("id"), "units": "mm",
            "items": result.items, "locators": result.locators, "floor": floor,
            "interfaces": interfaces(project, result.items)}
