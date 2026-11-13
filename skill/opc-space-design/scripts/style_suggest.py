"""Deterministic design suggestions from user priorities and known site conditions."""

from collections import Counter, defaultdict
from pathlib import Path

from interior import WALL_KINDS
from model import InputError, label, number, object_fields, read_json


LIBRARY_PATH = Path(__file__).resolve().parents[1] / "references" / "design-library.json"
AREA_CHECK = {"key": "area_m2", "label": "实际可用面积和家具使用空间"}
PROHIBITED_USES = {"禁用打印": "打印", "不要绿植": "绿植"}


def list_labels(value, field):
    if not isinstance(value, list) or len(value) > 24:
        raise InputError(f"{field} 应为不超过 24 项的文本列表")
    return list(dict.fromkeys(label(item, field) for item in value))


def existing_uses(value, library):
    if not isinstance(value, dict) or len(value) > 24:
        raise InputError("provided 应为不超过 24 项的需求与沿用设施说明")
    result = {}
    for raw_need, detail in value.items():
        need = label(raw_need, "沿用设施的需求标签")
        need = library["need_aliases"].get(need, need)
        if need not in library["needs"]:
            raise InputError(f"沿用设施的需求标签「{need}」不在建议库中")
        detail = label(detail, f"{need}的现有设施说明")
        if need in result and result[need] != detail:
            raise InputError(f"同一需求「{need}」提供了不同的沿用说明，请合并为一项")
        result[need] = detail
    return result


def normalize(brief, library):
    brief = object_fields(brief, "space style area_m2 needs avoid conditions keep provided", "设计建议输入")
    space = label(brief.get("space"), "空间类型")
    if space not in library["spaces"]:
        raise InputError(f"space 应为 {' / '.join(library['spaces'])}")
    style = label(brief.get("style"), "设计风格")
    style = library["style_aliases"].get(style, style)
    area = brief.get("area_m2")
    if area is not None:
        area = number(area, "实际可用面积 m²", .01, 1_000_000)
    raw_needs = list_labels(brief.get("needs", []), "使用需求")
    needs = list(dict.fromkeys(library["need_aliases"].get(item, item) for item in raw_needs))
    avoid = list_labels(brief.get("avoid", []), "限制条件")
    conditions = object_fields(brief.get("conditions", {}), " ".join(library["conditions"]), "现场条件")
    if any(value is not None and type(value) is not bool for value in conditions.values()):
        raise InputError("现场条件应为 true、false 或 null")
    provided = existing_uses(brief.get("provided", {}), library)
    banned = {need: constraint for constraint, need in PROHIBITED_USES.items() if constraint in avoid}
    reuse = {need: detail for need, detail in provided.items() if need not in banned}
    return {"space": space, "style": style, "area_m2": area, "needs": needs, "avoid": avoid,
            "conditions": conditions, "keep": list_labels(brief.get("keep", []), "保留要求"),
            "provided": provided, "reuse": reuse, "banned": banned}


def candidates(context, library):
    style = context["style"]
    family = library["style_families"].get(style)
    area = context["area_m2"]
    conditions = context["conditions"]
    eligible, excluded = [], []
    for order, module in enumerate(library["modules"]):
        if context["space"] not in module["spaces"]:
            continue
        primary = module["needs"][0]
        if module["group"] != "layout" and primary in context["reuse"]:
            continue
        matches = [need for need in context["needs"] if need in module["needs"] and need not in context["reuse"]]
        if context["needs"] and not matches:
            continue
        required_needs = module.get("activation_needs", [])
        if required_needs and not any(need in matches for need in required_needs):
            continue
        styles = module["styles"]
        style_rank = 2 if style in styles else 1 if family in styles else 0 if not styles else -1
        if style_rank < 0 and not matches:
            continue
        required = module.get("requires", [])
        if style_rank == 0 and required and module["group"] == "objects" and not matches:
            continue
        conflicts = [f"已明确「{item}」" for item in context["avoid"] if item in module.get("avoid", [])]
        model = module["model"] or {}
        if "不动墙面" in context["avoid"] and ("wall_mount" in required or model.get("kind") in WALL_KINDS):
            conflicts.append("已明确「不动墙面」")
        conflicts += [f"不具备{library['conditions'][key]}" for key in required if conditions.get(key) is False]
        area_min = module.get("area_min_m2")
        if area is not None and area_min is not None and area < area_min:
            conflicts.append(f"项目面积低于该做法的 {area_min:g} m² 初筛范围")
        if conflicts:
            if matches:
                excluded.append({"id": module["id"], "name": module["name"], "status": "rejected",
                                 "needs": matches, "reason": "、".join(dict.fromkeys(conflicts)) + "。"})
            continue
        checks = [{"key": key, "label": library["conditions"][key]} for key in required if conditions.get(key) is None]
        if area is None and area_min is not None:
            checks.append(AREA_CHECK)
        eligible.append({"module": module, "matched_needs": matches, "checks": checks,
                         "style_rank": style_rank, "order": order})
    return eligible, excluded


def select(eligible, needs, groups, *, explicit):
    selected, covered, slots = [], set(), set()
    limit = 6 if explicit else 4

    def available():
        return [row for row in eligible if row["module"]["slot"] not in slots]

    def add(row):
        selected.append(row)
        slots.add(row["module"]["slot"])
        covered.update(row["matched_needs"])

    for need in needs:
        if len(selected) == limit:
            break
        if need in covered:
            continue
        choices = [row for row in available() if need in row["matched_needs"]]
        if choices:
            add(min(choices, key=lambda row: (bool(row["checks"]), row["module"]["needs"][0] != need,
                                              -len(set(row["matched_needs"]) - covered), len(row["checks"]),
                                              -row["style_rank"], row["order"])))

    def priority(row):
        return (bool(row["checks"]), -len(row["matched_needs"]), -row["style_rank"], len(row["checks"]), row["order"])

    represented = Counter(row["module"]["group"] for row in selected)
    if explicit:
        limit = min(limit, len(selected) + 2)
    for group in groups:
        if len(selected) == limit:
            break
        choices = [row for row in available() if row["module"]["group"] == group]
        if not represented[group] and choices:
            add(min(choices, key=priority))
            represented[group] += 1
    while not explicit and len(selected) < limit:
        choices = [row for row in available() if represented[row["module"]["group"]] < 2]
        if not choices:
            break
        row = min(choices, key=priority)
        add(row)
        represented[row["module"]["group"]] += 1
    return selected, covered


def decision_for(row):
    module = row["module"]
    needs = row["matched_needs"]
    reason = f"用于本次的「{'、'.join(needs)}」。" if needs else "与场地用途和本次设计方向相符。"
    if row["checks"]:
        reason += "采用前先确认" + "、".join(check["label"] for check in row["checks"]) + "。"
    else:
        reason += module["why"]
    return {"id": module["id"], "name": module["name"],
            "status": "conditional" if row["checks"] else "selected", "needs": needs, "reason": reason}


def suggest(brief):
    """Propose a small design program. Geometry remains a separate verification step."""
    library = read_json(LIBRARY_PATH)
    context = normalize(brief, library)
    eligible, excluded = candidates(context, library)
    requested = [key for key in context["needs"] if key in library["needs"] and key not in context["reuse"]]
    selected, covered = select(eligible, requested, library["groups"], explicit=bool(context["needs"]))
    covered.update(context["reuse"])
    decisions = [{"name": detail, "status": "reuse", "needs": [need],
                  "reason": f"沿用已确认的「{need}」设施，省去重复配置。"} for need, detail in context["reuse"].items()]
    decisions.extend(decision_for(row) for row in selected)
    decisions.extend(sorted(excluded, key=lambda row: not any(need not in covered for need in row["needs"]))[:4])

    grouped = defaultdict(list)
    pending = {}
    for row in selected:
        module = row["module"]
        checks = row["checks"]
        result = {key: module[key] for key in ("id", "name", "model", "why", "design", "next_check")}
        result.update(matched_needs=row["matched_needs"], pending_conditions=checks,
                      status="conditional" if checks else "concept_ready")
        if row["style_rank"] > 0:
            result["matched_style"] = (context["style"] if row["style_rank"] == 2
                                       else library["style_families"][context["style"]])
        grouped[module["group"]].append(result)
        for check in checks:
            pending.setdefault(check["key"], f"待核实{check['label']}。")
    for need in context["needs"]:
        if need in covered:
            continue
        if need not in library["needs"]:
            pending[need] = "保留用户原话，请结合具体活动判断。"
        else:
            blockers = [row["reason"] for row in excluded if need in row["needs"]]
            if blockers and not any(need in row["matched_needs"] for row in eligible):
                pending[need] = "当前没有匹配且可提议的做法。" + "".join(dict.fromkeys(blockers))
            else:
                pending[need] = "当前条目未覆盖这项用途，请结合场地和活动补充。"
    for need, constraint in context["banned"].items():
        if need in context["provided"]:
            pending[need] = f"已记录现有设施，本轮按「{constraint}」要求暂缓使用。"
    for constraint in context["avoid"]:
        if constraint not in library["avoid"]:
            pending[constraint] = "保留用户限制，需在选型与布局中逐项核对。"
    palette = library["finish_palettes"].get(context["style"], {})
    finishes = [{"surface": surface, **direction}
                for surface, direction in palette.items()
                if "低施工量" not in context["avoid"]
                and {"wall": "不动墙面", "floor": "不动地面"}[surface] not in context["avoid"]]
    if context["style"] not in library["styles"]:
        pending["style"] = "按用户描述补充材质、图案和物件搭配，再核对对应的使用与安装条件。"
    if selected:
        pending["geometry"] = "出图前核对净轮廓、开口、固定结构和实际通行要求。"
    return {"space": context["space"], "style": context["style"], "area_m2": context["area_m2"],
            "kept": context["keep"], "status": "ready" if selected or context["reuse"] else "no_match",
            "groups": [{"key": key, "name": name, "suggestions": grouped[key]}
                       for key, name in library["groups"].items() if grouped[key]],
            "finish_directions": finishes,
            "decisions": decisions,
            "pending": [{"key": key, "reason": reason} for key, reason in pending.items()]}
