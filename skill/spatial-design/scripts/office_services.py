"""Scale shared office amenities and match them to verified object references."""

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from math import ceil

from shapely.geometry import Polygon

from model import InputError, label, number, object_fields


CONDITIONS = {
    "power": "可用电源及接线点",
    "water": "可用给水点",
    "drainage": "排水及检修条件",
    "daylight": "活植所需光照",
    "maintenance": "清洁、补给或养护责任",
    "network": "可用网络与设备接入权限",
    "ventilation": "通风及散热条件",
}
USE_ALIASES = {
    "访客": "接待", "会客": "接待", "洽谈": "接待", "开会": "会议", "复印": "打印", "扫描": "打印",
    "通话": "安静", "专注": "安静", "展陈": "布展", "展示": "布展", "理线": "充电", "植物": "绿化",
    "用餐": "午餐", "就餐": "午餐", "做样": "协作", "打样": "协作", "标识": "导视", "指引": "导视",
    "共享打印": "打印", "打印扫描": "打印", "访客会客": "接待", "内部短会": "会议", "设备充电": "充电",
    "安静通话": "安静", "员工寄存": "寄存", "饮水补给": "饮水", "保洁清运": "保洁",
}
NO_PLUMBING = {"不接水", "不接管", "不接上下水"}
NO_POWER = {"避开用电", "不接电"}
NO_PRINT = {"禁用打印", "不打印", "无纸化"}
TEAM_SERVICE_ROLES = {"drinking", "recycling"}
TEAM_SPREAD_MM = 12_000
COMPACT_PAIRINGS = {
    "waste": ("recycling", "优先把垃圾桶与分类回收布置在同一取用区，保留不同投放口和清运空间。"),
    "entry": ("storage", "优先用同一处浅收纳服务寄放和常用储物，核对满挂和开柜后的通行。"),
}
OMISSION_REASONS = {
    "printing": "只有纸质工作或共享扫描需求时再安排独立打印站。",
    "charging": "只有等候、短会或共用设备确有充电需求时再安排独立充电站。",
    "quiet": "只有频繁通话或需要隔声时再比较独立通话舱和已有可关闭空间。",
    "lunch": "明确有现场就餐和相应停留空间时再安排专门餐桌。",
}


@dataclass(frozen=True)
class Role:
    key: str
    name: str
    kinds: tuple[str, ...]
    note: str
    checks: tuple[str, ...]
    uses: tuple[str, ...] = ()
    capacity: int = 0
    spread_m2: int = 0
    conditions: tuple[str, ...] = ()
    baseline: bool = False


ROLES = (
    Role("drinking", "饮水与杯具", ("water_station",),
         "靠近实际取用和补给路径，杯具与接水面放在同一处，停留点让开主通道。",
         ("核对设备外廓、接水盘、换桶或检修空间。",), ("饮水",), capacity=55, spread_m2=650, baseline=True),
    Role("recycling", "分类回收", ("recycling_station",),
         "靠近茶水或公共补给点，取水和投放可连续完成。",
         ("按实际回收种类核对开盖、抽桶和清运路线。",), ("分类回收",),
         capacity=70, spread_m2=750, conditions=("maintenance",), baseline=True),
    Role("waste", "日常废弃物", ("waste_bin",),
         "办公组附近的日常垃圾点按清运路径组织，湿垃圾可集中到茶水侧。",
         ("确认物业的收运分类、桶体容量和清洁频次。",), ("垃圾",),
         capacity=35, spread_m2=400, conditions=("maintenance",), baseline=True),
    Role("entry", "入口随身物收纳", ("entry_organizer",),
         "在常用入口侧留外套、包和雨伞的落点，使用面避开门扇和主路线。",
         ("核对哪些入口供日常使用，并按满挂后外廓和接水盘留空间。",), ("寄存",), baseline=True),
    Role("signage", "入口导视与公告", ("notice_board",),
         "在访客到达后看得见的位置标明接待和主要分区，公告面留出停读空间。",
         ("逐一核对入口、转向点、标识内容和观看方向。",), ("导视",)),
    Role("cleaning", "保洁备品", ("cleaning_cabinet",),
         "把拖具和耗材集中在可上锁的维护点，保洁从工作区边缘补给。",
         ("确认物业共用设施、清运方向和清洁品分格。",), ("保洁",),
         capacity=85, spread_m2=800, conditions=("maintenance",), baseline=True),
    Role("storage", "个人与公共收纳", ("locker_bank", "storage"),
         "按团队或公共用途集中柜体，少量高频取用留在使用组附近。",
         ("按使用者人数和物件尺寸复核格口数量、开门和柜体固定。",), ("收纳",),
         capacity=32, spread_m2=520, baseline=True),
    Role("printing", "共享打印与扫描", ("print_station",),
         "纸档业务使用共享设备，进纸、取件和耗材维护集中在工位边缘。",
         ("确认打印扫描频率、纸张存放、维护侧和设备散热。",),
         ("打印",), capacity=75, spread_m2=950, conditions=("power",)),
    Role("charging", "共享充电与理线", ("charging_station",),
         "在短时协作、等候或随身物取放的一侧安排充电点，线缆沿设备背侧归位。",
         ("核对设备接入方式、回路容量和走线位置。",),
         ("充电",), capacity=65, spread_m2=850, conditions=("power",)),
    Role("reception", "访客接待", ("sofa", "counter"),
         "从常用访客入口可以看见接待位置，在工位区外完成问询或等候。",
         ("按来访高峰核对座位、驻足空间和入口视线。",), ("接待",)),
    Role("meeting", "讨论和会议", ("meeting_table_4", "meeting_table_6", "meeting_table_8"),
         "按并发人数选择共用会议桌或多个讨论点，保留各桌座椅使用面。",
         ("核对会面人数、是否需要关门和设备使用。",), ("会议",)),
    Role("quiet", "安静工作与通话", ("focus_pod",),
         "优先了解已有可关闭空间，再在离制作、打印和公共取用较远的位置比较通话舱。",
         ("按实物核对通风、隔声、开门和搬入。",), ("安静",), conditions=("power", "ventilation")),
    Role("display", "布展与可换样品", ("gallery", "display", "display_wall"),
         "将更新频繁的展面和备样位置靠近，访客停留面让开通行。",
         ("确认展品尺寸、挂点承载和取换范围。",), ("布展",)),
    Role("green", "绿化与养护", ("potted_plant", "planter"),
         "在可维护的采光位置少量布置，集中浇养面和托盘。",
         ("核对窗扇、叶片外展、接水和养护路径。",), ("绿化",), conditions=("daylight", "maintenance")),
    Role("lunch", "午餐与短休", ("dining",),
         "按用餐时段安排坐席和易清洁的使用面，使用后可恢复公共活动。",
         ("确认就餐并发、拉椅及清洁。若需加热或加工，另核设备和现场接口。",), ("午餐",)),
    Role("collaboration", "共创与做样", ("workshop",),
         "把会走动、会交流的共创或评审活动安排在团队边缘的共享面。",
         ("核对台面载荷、物料外廓、取放空间和噪声。",), ("协作",)),
)


def text_list(value, field):
    if not isinstance(value, list) or len(value) > 32:
        raise InputError(f"{field}应为不超过 32 项的文本列表")
    return list(dict.fromkeys(label(item, field) for item in value))


def parse_brief(project, brief, items):
    raw = object_fields({} if brief is None else brief, "peak_people uses conditions avoid retained defer shared",
                        "办公配套说明")
    people = number(raw.get("peak_people", project.req["desks"]), "峰值在场人数", 0, 10000, integer=True)
    uses = {USE_ALIASES.get(use, use) for use in text_list(raw.get("uses", []), "办公用途")}
    avoid = set(text_list(raw.get("avoid", []), "办公限制"))
    conditions = object_fields(raw.get("conditions", {}), " ".join(CONDITIONS), "办公现场条件")
    if any(value is not None and type(value) is not bool for value in conditions.values()):
        raise InputError("办公现场条件应为 true、false 或 null")
    role_keys = " ".join(role.key for role in ROLES)
    defer = object_fields(raw.get("defer", {}), role_keys, "办公暂缓事项")
    defer = {key: label(reason, f"暂缓 {key} 的原因") for key, reason in defer.items()}
    shared = object_fields(raw.get("shared", {}), role_keys, "已确认共用设施")
    shared = {key: label(location, f"共用 {key} 的位置和使用条件") for key, location in shared.items()}
    if defer.keys() & shared.keys():
        raise InputError("同一办公配套须明确选择暂缓或共用")
    retained = parse_retained(project, raw.get("retained", []), items)
    return people, uses, avoid, conditions, defer, shared, retained


def parse_retained(project, retained, items):
    if not isinstance(retained, list) or len(retained) > 32:
        raise InputError("原有办公配套应为不超过 32 项的列表")
    available = {f"S{index:03d}" for index, (_, kind, _) in enumerate(project.fixed, 1)
                 if kind in {"existing", "service"}}
    if items is not None:
        available &= {item["id"] for item in items if item["category"] == "fixed"}
    kinds = {kind for role in ROLES for kind in role.kinds}
    result = {}
    for row in retained:
        object_fields(row, "ref kind", "原有办公配套")
        ref = label(row.get("ref"), "原有配套编号")
        kind = label(row.get("kind"), "原有配套类型")
        if ref not in available or kind not in kinds or ref in result:
            raise InputError(f"原有办公配套 {ref} 须对应可核对的固定设施编号和一种配套类型，编号不得重复")
        result[ref] = kind
    return result


def spread_out_work_groups(project, people, area_m2):
    if people < 20 or area_m2 < 350:
        return {}
    centers = {name: group["polygon"].representative_point() for name, group in project.req["work_groups"].items()}
    return centers if any(a.distance(b) >= TEAM_SPREAD_MM for a, b in combinations(centers.values(), 2)) else {}


def team_coverage_check(refs, indexed, teams):
    assigned = []
    for ref in refs:
        polygon = indexed.get(ref, {}).get("polygon")
        if polygon:
            point = Polygon(polygon).representative_point()
            assigned.append((min(teams, key=lambda name: point.distance(teams[name])), point))
    if len(assigned) < 2:
        return "已有点位的位置证据不足，请复核相隔工区的实际取用路线。"

    # A pair split across a notional group boundary may still be physically adjacent.
    for (a, point_a), (b, point_b) in combinations(assigned, 2):
        team_gap = teams[a].distance(teams[b])
        if team_gap >= TEAM_SPREAD_MM and point_a.distance(point_b) >= team_gap / 3:
            return None

    nearest = {name for name, _ in assigned}
    other_teams = [name for name, center in teams.items() if name not in nearest
                   and any(center.distance(teams[close]) >= TEAM_SPREAD_MM for close in nearest)]
    if other_teams:
        names = "、".join(other_teams)
        return f"按平面直线位置，点位偏向同一侧。请复核「{names}」的实际取用路线和高峰共享条件。"
    return "按平面直线位置，点位仍较集中。请复核相隔工区的实际取用路线和高峰共享条件。"


def role_targets(project, role, people, area_m2, separated_teams):
    if role.key in {"entry", "signage"}:
        return max(1, len(project.req["visitor_entries"]))
    if role.key == "meeting":
        return max(1, len(project.req["meeting_seats"]))
    if not role.capacity:
        return 1
    # Long walking distances may favor a second point, even before the first
    # is full. Cap that effect by the number of people actually present.
    people_points = ceil(people / role.capacity)
    spread_points = min(ceil(area_m2 / role.spread_m2), ceil(2 * people / role.capacity))
    group_points = 2 if separated_teams and role.key in TEAM_SERVICE_ROLES else 1
    return max(group_points, people_points, spread_points)


def drinking_conditions(conditions, avoid):
    may_power = not (NO_POWER & avoid) and conditions.get("power") is not False
    if not may_power:
        return ("maintenance",), "可先按手动桶装饮水和杯具留位，采用人工补水。", None
    required = ("power", "maintenance")
    plumbing = (conditions.get("water"), conditions.get("drainage"))
    if NO_PLUMBING & avoid:
        prefix = "按不接水要求，"
    elif False in plumbing:
        prefix = "现场不具备完整给排水，"
    elif plumbing == (True, True):
        return required, "现场给排水已提供，可比较接管直饮。", None
    else:
        return (required, "可先按桶装饮水设备留位，补给与换桶路径一并考虑。",
                "如比较接管直饮，再核实尚未确认的给水和排水条件。")
    return required, prefix + "采用桶装饮水，留出补给与换桶路径。", None


def role_conditions(role, conditions, avoid):
    note = role.note
    checks = list(role.checks)
    required = role.conditions
    if role.key == "drinking":
        required, method, alternative_check = drinking_conditions(conditions, avoid)
        note += method
        if alternative_check:
            checks.append(alternative_check)
    if role.key == "printing":
        network = conditions.get("network")
        if network is False:
            checks.append("现场无共享打印网络时，可核设备是否支持 USB 直连，再确认单位接入权限。")
        elif network is None:
            checks.append("共享打印网络和接入权限待核，可先比较支持 USB 直连的设备。")
    unavailable = [CONDITIONS[key] for key in required if conditions.get(key) is False
                   or key == "power" and NO_POWER & avoid]
    pending = [CONDITIONS[key] for key in required if conditions.get(key) is None
               and not (key == "power" and NO_POWER & avoid)]
    checks.extend(f"现场缺少{condition}。" for condition in unavailable)
    checks.extend(f"待核实{condition}。" for condition in pending)
    return note, checks, unavailable, pending


def status_for(refs, target, known_blockers, pending, actual, deferred):
    if deferred:
        return "按要求暂缓"
    if known_blockers:
        return "现场受限"
    if refs:
        if len(refs) < target:
            return "部分安排"
        return "已安排待核" if pending else "已安排"
    if pending:
        return "条件待核"
    return "待安排" if actual else "建议安排"


def service_decision(row, shared, defer, compact):
    key, status = row["key"], row["status"]
    if status == "共用":
        return "reuse", row["note"]
    if status == "按要求暂缓":
        return ("hold" if key in defer else "omit"), row["note"]
    if status in {"现场受限", "条件待核", "已安排待核", "部分安排"}:
        if status == "部分安排":
            return "hold", f"图中已有 {len(row['refs'])} 处，先复核分布和实际使用再决定是否补足。"
        return "hold", " ".join(row["checks"])
    if status == "已安排":
        return "keep", row["note"]
    paired = COMPACT_PAIRINGS.get(key)
    if compact and paired and paired[0] not in shared and paired[0] not in defer:
        return "combine", paired[1]
    return "add", row["note"]


def office_services(project, brief, items=None):
    """Return suggested service points, or count exact placement/retained refs if provided."""
    people, uses, avoid, conditions, defer, shared, retained = parse_brief(project, brief, items)
    area = project.outline.area / 1_000_000
    compact = 0 < people <= 6 and area <= 65
    separated_teams = spread_out_work_groups(project, people, area)
    indexed = {item["id"]: item for item in items or []}
    references = defaultdict(list)
    for item in items or []:
        if item["category"] in {"furniture", "interior"}:
            references[item["kind"]].append(item["id"])
    for ref, kind in retained.items():
        references[kind].append(ref)

    configured = {item["kind"] for item in project.req["furniture"].values()}
    configured.update(item["kind"] for item in (project.req["interior"] or {}).get("elements", []))
    if project.req["lounge"]:
        configured.add("sofa")
    configured.update(f"meeting_table_{n}" for n in project.req["meeting_seats"])
    defaults = {
        "printing": bool(NO_PRINT & avoid),
        "reception": bool(project.req["visitor_entries"]),
        "signage": bool(project.req["visitor_entries"]),
        "green": project.req["style"] in {"绿化", "自然疗愈"} or bool({"不要绿植", "免浇养"} & avoid),
    }
    rows, decisions = [], []
    for role in ROLES:
        refs = [ref for kind in role.kinds for ref in references[kind]]
        decision = {"key": role.key, "name": role.name, "refs": refs}
        if not (role.baseline or defaults.get(role.key) or role.key in defer or role.key in shared or refs
                or uses.intersection(role.uses) or configured.intersection(role.kinds)):
            decisions.append({**decision, "decision": "omit", "reason": OMISSION_REASONS.get(
                role.key, "当前没有对应活动或设施，保持空间给已确认的用途。")})
            continue
        reason = defer.get(role.key)
        if role.key == "printing" and NO_PRINT & avoid:
            reason = reason or "按无纸化或禁用打印要求暂缓。"
        if role.key == "green" and {"不要绿植", "免浇养"} & avoid:
            reason = reason or "按免养护或不设绿植的要求暂缓。"
        share = shared.get(role.key) if not reason else None
        target = 0 if reason or share else role_targets(project, role, people, area, separated_teams)
        note, checks, unavailable, pending = role_conditions(role, conditions, avoid)
        if reason:
            note, checks = reason, []
        elif share:
            note = f"沿用已确认可共用的设施。{share}"
            checks = ["复核共用设施的开放时间、到达路线和高峰容量。"]
            if refs:
                checks.append("当前布局也有同类点位。比较保留价值和使用冲突，再决定是否移除。")
        elif separated_teams and role.key in TEAM_SERVICE_ROLES:
            note += "相隔的团队区可分设取用点。若同一个点位便于共同到达，可合并。"
            if items is not None and len(refs) >= target:
                distribution = team_coverage_check(refs, indexed, separated_teams)
                if distribution:
                    checks.append(distribution)
                    pending.append(distribution)
        elif target > 1:
            note += "建议点位可按分区合并或分设，数量随实际使用复核。"
        if any(ref in retained for ref in refs):
            checks.append("保留设施依据用户指定编号计入，核对其实际设备和可用状态。")
        if people == 0 and not reason:
            checks.append("峰值人数待确认，当前数量为讨论起点。")
        row = {"key": role.key, "name": role.name, "kinds": list(role.kinds), "target": target,
               "refs": refs, "status": "共用" if share else
               status_for(refs, target, unavailable, pending, items is not None, reason),
               "note": note, "checks": checks}
        locations = [{"ref": ref, "zone": indexed[ref]["zone"]} for ref in refs if indexed.get(ref, {}).get("zone")]
        if locations:
            row["locations"] = locations
        rows.append(row)
        outcome, explanation = service_decision(row, shared, defer, compact)
        decisions.append({**decision, "decision": outcome, "reason": explanation})
    recognized = {use for role in ROLES for use in role.uses}
    return {"people": people, "area_m2": round(area, 3), "rows": rows, "decisions": decisions,
            "unmapped_uses": sorted(uses - recognized)}
