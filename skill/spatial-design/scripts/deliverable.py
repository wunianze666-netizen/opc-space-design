"""Create the readable cover and file manifest of a selected scheme."""

from hashlib import sha256

from model import write_json
from positioning import millimetres


def write_delivery(stage, data, ranking, selected, rendering, explicit):
    position = data["positioning"]
    tied = ranking["tied_first_options"]
    reasons = next(row["reasons"] for row in ranking["ranking"] if row["id"] == selected)
    why = "本次按指定方案交付。" if explicit else (
        "本次按项目优先项选出方案。" if len(tied) <= 1 else
        f"按现有指标，方案 {'、'.join(tied)} 并列。本包采用 {selected} 作为摆放参考。")
    if not reasons:
        reasons = ["现有优先项缺少可比较的依据，查看下方待核项。"]
    pending = [row["name"] for row in data["needs"] if row["status"] == "待确认"]
    pending.extend(row["name"] for row in data.get("office_services", {}).get("rows", [])
                   if row["status"] in ("已安排待核", "条件待核", "现场受限", "待安排", "部分安排"))
    model = "可在整区总览打开 Blender 渲染。" if rendering == "rendered" else "可在整区总览查看同坐标三维。"
    if data.get("local_materials", {}).get("applied"):
        model = "可在整区总览打开本地贴图三维。"
    if rendering == "fallback":
        model += " 本次 Blender 渲染待完成。"
    a, b = position["origin_mm"], position["reference_mm"]
    lines = [f"# {data['title']} · 方案 {selected}", "", data["summary"], "", why,
             *[f"- {reason}" for reason in reasons], ""]
    skipped = ranking["skipped_priorities"]
    if skipped:
        lines.extend([*(f"- {reason}" for reason in skipped.values()), ""])
    lines.extend([
        "## 按图摆放", "",
        "1. 打开 [整区总览](整区总览.html)，点选平面、三维或编号，查看物件轮廓、尺寸和操作侧。",
        f"2. 在总览中找图上的 A、B 两个底图轮廓点。先现场核对两点间直线距离 "
        f"{millimetres(position['reference_distance_mm'])} mm，再对照另一条已知尺寸，确认相同的 X、Y 方向。",
        "3. 从 A 按图上的 X、Y 方向量取《逐件摆放.csv》中的点位，单位为 mm。墙面项目以线段两端定位，"
        "家具和地面以各轮廓点定位，带底顶标高的项目再核对高度。旋转角是模板角度，操作侧以总览的标线为准。",
        "", f"图纸原坐标 A = ({millimetres(a[0])}, {millimetres(a[1])}) mm，"
        f"B = ({millimetres(b[0])}, {millimetres(b[1])}) mm。"
        "现场只能在确认可识别这两个点和图纸方向后使用相对坐标。", "",
        "## 同版文件", "",
        "- [逐件摆放 CSV](逐件摆放.csv) 与总览共用物件编号。",
        "- [尺寸平面 PNG](平面图.png) 与 [毫米 DXF](平面图.dxf) 供查看和专业复核。",
        f"- [整区预览](整区总览.png)。{model}",
    ])
    if (stage / "配套局部.png").is_file():
        lines.append("- [配套局部图](配套局部.png) 可放大查看设备形态和相对位置。")
    if (stage / "方案比选.png").is_file():
        lines.append("- [候选方案比选](方案比选.png) 可查看其余通过校核的布局。")
    lines.extend(["", "## 现场待核", "",
                  "以现场复尺确认门洞、净高、家具实际外廓、固定点和设备接口。涉及施工的结构、消防、机电和安装节点需相应专业深化。"])
    if pending:
        lines.append("当前仍需确认 " + "、".join(dict.fromkeys(pending)) + "。")
    lines.extend(["", f"底图来源 · {data['source']}", f"布局版本 · {data['layout_sha256']}"])
    (stage / "先看这里.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    info = {"option": selected, "recommendation": ranking, "chosen_explicitly": explicit,
            "layout_sha256": data["layout_sha256"], "units": "mm", "positioned_objects": len(position["rows"]),
            "rendering": rendering or ("local_materials" if data.get("local_materials", {}).get("applied")
                                        else "interactive_axon"),
            "files": {path.name: sha256(path.read_bytes()).hexdigest() for path in sorted(stage.iterdir()) if path.is_file()}}
    write_json(stage / "交付校核.json", info)
    return info
