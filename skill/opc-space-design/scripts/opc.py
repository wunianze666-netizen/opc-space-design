"""Command-line entry point for the OPC space design skill."""

import argparse
import csv
import hashlib
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from intake import NAMED_UNITS, calibrate, import_dxf, inspect
from layout import STRATEGIES, TEMPLATES, rank_options, search, validate
from model import PRIORITIES, InputError, digest, object_fields, parse, read_json, write_json


def new_path(path):
    path = path.resolve()
    if path.exists():
        raise InputError(f"输出已存在，请指定新路径：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def status_for(options, wanted):
    return "no_layout" if not options else "ready" if len(options) >= wanted else "partial"


def rows_for(project, selected=None, previous=None):
    if previous and all(p["type"] in TEMPLATES or p["type"] in project.req["furniture"] for p in previous):
        current = validate(project, previous)
        if current["passed"]:
            return [(selected, previous, current)], {"attempts": 0, "preserved_layout": True}
    solutions, stats = search(project, selected=selected, previous=previous)
    if previous and not solutions:
        fallback, retry = search(project, selected=selected)
        retry["initial_attempts"] = stats["attempts"]
        retry["initial_rejections"] = stats.get("rejected", {})
        retry["attempts"] += stats["attempts"]
        retry["searched_without_position_preference"] = True
        return fallback, retry
    return solutions, stats


def checksum(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def checked_layouts(project, records):
    if not isinstance(records, list) or not 1 <= len(records) <= project.req["options"]:
        raise InputError("布局候选须为列表，数量不得超过项目要求的方案数")
    solutions, seen, rejected, examples = [], set(), {}, {}
    for record in records:
        object_fields(record, "strategy placements", "布局候选")
        strategy = record.get("strategy")
        if not isinstance(strategy, str) or strategy not in STRATEGIES:
            raise InputError("布局候选 strategy 应为 open / hybrid / focus")
        positions = record.get("placements")
        audit = validate(project, positions)
        if audit["passed"]:
            signature = tuple(sorted((p["type"], p["x"], p["y"], p["angle"]) for p in positions))
            if signature in seen:
                raise InputError("布局候选位置完全相同，请保留一套")
            seen.add(signature)
            solutions.append((strategy, positions, audit))
        else:
            for row in audit["checks"]:
                if row["status"] == "fail":
                    rule = row["id"]
                    rejected[rule] = rejected.get(rule, 0) + 1
                    examples.setdefault(rule, row)
    return solutions, {"attempts": len(records), "validated_candidates": len(solutions),
                       "rejected": rejected, "examples_of_rejections": examples, "input": "submitted_layouts"}


def output_bundle(project, out, *, selected=None, previous=None, parent=None, views=True,
                  brief=False, quality="quick", blender=None, submitted=None):
    from alignment import verify_alignment
    from board import write_board
    from export import option_files, reports

    out = new_path(out)
    solutions, stats = (rows_for(project, selected, previous) if submitted is None
                        else checked_layouts(project, submitted))
    wanted = 1 if selected else project.req["options"]
    project_hash = digest(project.raw)
    revision = project_hash[:10]
    options = []
    for idx, (strategy, placements, audit) in enumerate(solutions):
        body = {"id": chr(65 + idx), "strategy": strategy, "placements": placements,
                "audit": audit, "project_sha256": project_hash}
        body["layout_sha256"] = digest(body)
        options.append(body)
    status = status_for(options, wanted)
    recommendation = rank_options(project, options)
    if status == "no_layout" and not stats.get("reason"):
        failures = "、".join(f"{key} {value}次" for key, value in stats.get("rejected", {}).items())
        stats["reason"] = ("当前布局搜索未找到通过已配置约束的候选。请核对工位、配套、出口、净宽或预算。"
                           + (f" 搜索记录：{failures}。" if failures else ""))
        quote = stats.get("examples_of_rejections", {}).get("budget", {})
        holes = stats.get("examples_of_rejections", {}).get("containment", {}).get("void_conflicts", [])
        if holes:
            names = "、".join(dict.fromkeys(row["void"] for row in holes))
            stats["reason"] += f" 有家具或使用范围进入无楼板区 {names}。"
        if quote.get("missing_prices"):
            stats["reason"] += " 需要补价：" + "、".join(quote["missing_prices"]) + "。"
        if quote.get("limit_cents") is not None and quote["priced_subtotal_cents"] > quote["limit_cents"]:
            stats["reason"] += (f" 一个被拒候选的已报价小计为 {quote['priced_subtotal_cents'] / 100:,.2f} 元，"
                                f"高于配置上限 {quote['limit_cents'] / 100:,.2f} 元。")
    manifest = {"schema": 1, "status": status, "revision": revision, "project_sha256": project_hash,
                "requested_options": wanted, "parent": parent, "search": stats, "recommendation": recommendation,
                "options": [{"id": o["id"], "strategy": o["strategy"], "layout_sha256": o["layout_sha256"],
                             "subtotal_cents": o["audit"]["subtotal_cents"],
                             "missing_prices": o["audit"]["missing_prices"]} for o in options]}
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".opc-") as work:
        stage = Path(work) / "bundle"
        stage.mkdir()
        write_json(stage / "project.json", project.raw)
        manifest["alignment"] = []
        for option in options:
            option_files(stage, project, option, views, plan_pdf=not brief)
            key = option["id"]
            filename = f"{key}/几何对齐.json"
            evidence = verify_alignment(project, option["placements"], stage / key / "平面图.dxf")
            write_json(stage / filename, evidence)
            manifest["alignment"].append({"id": key, "file": filename, "layout_sha256": option["layout_sha256"]})
        rendering = {"status": "not_requested", "engine": "local_axon"}
        if quality == "studio" and options:
            from studio import render_options
            rendering = render_options(stage, project, options, blender)
        manifest["rendering"] = rendering
        manifest["board"] = {"png": "方案一页比选.png", "pdf": "方案一页比选.pdf"}
        board_stats = {**stats, "requested_options": wanted, "status": status, "rendering": rendering,
                       "alignment_verified": bool(manifest["alignment"])}
        write_board(stage, project, options, board_stats, revision, recommendation)
        if not brief:
            reports(stage, project, options, stats, revision, recommendation)
        manifest["files"] = {p.relative_to(stage).as_posix(): checksum(p)
                             for p in sorted(stage.rglob("*")) if p.is_file()}
        write_json(stage / "manifest.json", manifest)
        with ZipFile(stage / "方案包.zip", "w", compression=ZIP_DEFLATED) as archive:
            for name in [*manifest["files"], "manifest.json"]:
                archive.write(stage / name, name)
        if out.exists():
            raise InputError(f"输出路径已被其他任务使用：{out}")
        stage.rename(out)
    return {"out": str(out), **{key: manifest[key] for key in
                                ("status", "revision", "requested_options", "options", "search",
                                 "recommendation", "board", "rendering", "alignment")}}


def verify(folder):
    manifest = read_json(folder / "manifest.json")
    project = parse(read_json(folder / "project.json"))
    if digest(project.raw) != manifest["project_sha256"]:
        raise InputError("项目输入与导出记录不一致")
    for name, sha in manifest["files"].items():
        path = (folder / name).resolve()
        if not path.is_relative_to(folder.resolve()) or not path.is_file() or checksum(path) != sha:
            raise InputError(f"方案文件缺失或已被修改：{name}")
    checked = manifest.get("alignment")
    if checked is not None:
        expected = [{"id": row["id"], "file": f"{row['id']}/几何对齐.json", "layout_sha256": row["layout_sha256"]}
                    for row in manifest["options"]]
        if checked != expected or any(row["file"] not in manifest["files"] for row in checked):
            raise InputError("几何对齐记录与方案版本不一致")
        from alignment import verify_alignment
    layouts = []
    for info in manifest["options"]:
        if info["id"] not in ("A", "B", "C"):
            raise InputError("方案编号无效")
        layout = read_json(folder / info["id"] / "layout.json")
        expected = layout.pop("layout_sha256")
        if digest(layout) != expected or expected != info["layout_sha256"] or layout["project_sha256"] != manifest["project_sha256"]:
            raise InputError("方案与项目版本不对应")
        audit = validate(project, layout["placements"])
        if not audit["passed"] or digest(audit) != digest(layout["audit"]):
            raise InputError("重新检查发现几何或材料清单不一致")
        layouts.append(layout)
        if checked is not None:
            option_dir = folder / info["id"]
            fresh = verify_alignment(project, layout["placements"], option_dir / "平面图.dxf")
            if digest(fresh) != digest(read_json(option_dir / "几何对齐.json")):
                raise InputError("重新检查发现图纸或三维几何记录不一致")
    rendering = manifest.get("rendering", {})
    if rendering.get("status") == "rendered":
        expected = [(row["id"], row["layout_sha256"], f"{row['id']}/精渲染.png")
                    for row in manifest["options"]]
        actual = [(row["id"], row.get("layout_sha256"), row["file"]) for row in rendering.get("images", [])]
        if actual != expected or any(row[2] not in manifest["files"] for row in actual):
            raise InputError("精渲染图的编号或布局版本不一致")
        scene_hashes = {row["id"]: row.get("scene_sha256") for row in rendering["images"]}
        if any(value is not None for value in scene_hashes.values()):
            from studio import studio_scene_inputs
            _, _, rebuilt = studio_scene_inputs(project, layouts)
            if scene_hashes != rebuilt:
                raise InputError("Blender 输入场景与已校核布局不一致")
        if any(row.get("eye_file") or row.get("eye_scene_sha256") for row in rendering["images"]):
            from studio import eye_scene_inputs
            _, _, rebuilt_eye = eye_scene_inputs(project, layouts)
            eyes = {row["id"]: row.get("eye_scene_sha256") for row in rendering["images"]}
            if eyes != rebuilt_eye or any(row.get("eye_file") != f"{row['id']}/人视效果.png"
                                         or row["eye_file"] not in manifest["files"] for row in rendering["images"]):
                raise InputError("人视图的机位或三维场景与布局版本不一致")
    if manifest.get("board") and not all(file in manifest["files"] for file in manifest["board"].values()):
        raise InputError("一页比选与导出记录不一致")
    with ZipFile(folder / "方案包.zip") as archive:
        expected_files = [*manifest["files"], "manifest.json"]
        if sorted(archive.namelist()) != sorted(expected_files):
            raise InputError("ZIP 中的文件列表与交付目录不一致")
        for name in expected_files:
            with archive.open(name) as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != checksum(folder / name):
                    raise InputError(f"ZIP 中的文件与交付目录不一致：{name}")
    result = {"verified": True, "status": manifest["status"], "options": len(manifest["options"]),
              "file_count": len(manifest["files"]) + 2}
    if checked is not None:
        result["geometry_checked_options"] = len(checked)
    return result


def revise(folder, option, changes, out, **presentation):
    verify(folder)
    snapshot = read_json(folder / "project.json")
    selected = read_json(folder / option / "layout.json")
    changes = object_fields(changes, "requirements prices title", "修改内容")
    new_project = deepcopy(snapshot)
    for key, value in changes.items():
        if key == "requirements":
            if not isinstance(value, dict):
                raise InputError("requirements 修改须为对象")
            new_project[key].update(value)
        else:
            new_project[key] = value
    return output_bundle(parse(new_project), out, selected=selected["strategy"],
                         previous=selected["placements"],
                         parent={"project_sha256": digest(snapshot), "option": option,
                                 "layout_sha256": selected["layout_sha256"]}, **presentation)


def recommend(folder, priorities=None):
    verify(folder)
    raw = read_json(folder / "project.json")
    if priorities:
        raw["requirements"]["priorities"] = priorities
    project = parse(raw)
    manifest = read_json(folder / "manifest.json")
    options = [read_json(folder / entry["id"] / "layout.json") for entry in manifest["options"]]
    return {"status": manifest["status"], **rank_options(project, options)}


def overview(folder, option, out, brief=None, asset_root=None, *, quality="quick", blender=None, azimuth=None,
             with_positions=False):
    from overview import overview_data
    from overview_view import write_overview

    if out.resolve().is_relative_to(folder.resolve()):
        raise InputError("整区总览目录须位于原方案包之外")
    verify(folder)
    manifest = read_json(folder / "manifest.json")
    if option not in {row["id"] for row in manifest["options"]}:
        raise InputError(f"方案 {option} 未通过校核或不存在")
    project = parse(read_json(folder / "project.json"))
    source = folder / option
    layout = read_json(source / "layout.json")
    swatches = {}
    data = overview_data(project, layout, brief, asset_root, swatches, with_positions=with_positions)
    out = new_path(out)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".opc-overview-") as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir()
        rendering = None
        axon_path = source / "轴测-1.png"
        if quality == "studio":
            from studio import EYE_NAME, RENDER_NAME, render_options

            (stage / option).mkdir()
            patterns = [row for row in data.get("patterns", []) if row["status"] == "ready"]
            rendering = render_options(stage, project, [layout], blender, patterns=patterns, swatches=swatches,
                                       azimuth=azimuth, model_name="同坐标饰面.blend")
            for record in rendering["images"]:
                for key, source_name, target in (("file", RENDER_NAME, "纹样三维.png"),
                                                 ("eye_file", EYE_NAME, "墙地顶人视.png")):
                    if key in record:
                        (stage / option / source_name).replace(stage / target)
                        record[key] = target
                for row in patterns:
                    row["studio_views"] = [view for view, field in (("整区三维", "cutaway_surfaces"),
                                                                    ("墙地顶人视", "eye_surfaces"))
                                           if row["ref"] in record.get(field, {})]
            (stage / option).rmdir()
            data["studio"] = rendering
            write_json(stage / "饰面三维校核.json", rendering)
            if rendering["images"]:
                axon_path = stage / "纹样三维.png"
        if swatches and axon_path.parent != stage:
            from local_materials import render_cutaway

            applied, hidden, angle = render_cutaway(stage / "本地贴图三维.png", project, layout, data["patterns"],
                                                   swatches, azimuth)
            if applied:
                axon_path = stage / "本地贴图三维.png"
                data["local_materials"] = {"file": axon_path.name, "azimuth_degrees": angle,
                                           "applied": applied, "out_of_view": hidden}
                for row in data["patterns"]:
                    if row["ref"] in applied:
                        row["local_view"] = True
            else:
                data["local_materials"] = {"status": "no_visible_surfaces", "out_of_view": hidden}
        write_json(stage / "整区物件.json", data)
        write_overview(stage, data, source / "平面图.png", axon_path)
        if "detail_views" in data:
            from overview_detail import DETAIL_NAME, detail_board
            detail_board(project, layout, data).save(stage / DETAIL_NAME)
        if with_positions:
            from positioning import write_positions
            write_positions(stage / "逐件摆放.csv", data["positioning"])
        files = sorted(file.name for file in stage.iterdir() if file.is_file())
        if out.exists():
            raise InputError(f"输出路径已被其他任务使用：{out}")
        stage.rename(out)
    result = {"out": str(out), "status": "ready", "option": option, "objects": len(data["items"]),
            "zones": len(data["zones"]), "pending_needs": [n["name"] for n in data["needs"] if n["status"] == "待确认"],
            "files": files}
    if rendering is not None:
        result["rendering"] = rendering["status"]
        if "reason" in rendering:
            result["rendering_reason"] = rendering["reason"]
    return result


def deliver(folder, option, out, brief=None, asset_root=None, *, quality="quick", blender=None, azimuth=None):
    """Publish one verified recommendation with the files needed to read and place it."""
    import shutil
    from alignment import verify_alignment
    from deliverable import write_delivery

    if out.resolve().is_relative_to(folder.resolve()):
        raise InputError("交付目录须位于原方案包之外")
    ranking = recommend(folder)
    selected = option or ranking["recommended_option"]
    if selected is None:
        raise InputError("当前没有可自动推荐的方案，请检查候选或用 --option 指定已通过校核的方案")
    if not any(row["id"] == selected for row in ranking["ranking"]):
        raise InputError(f"方案 {selected} 未通过校核或不存在")
    out = new_path(out)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".opc-delivery-") as temporary:
        stage = Path(temporary) / "bundle"
        answer = overview(folder, selected, stage, brief, asset_root, quality=quality, blender=blender,
                          azimuth=azimuth, with_positions=True)
        manifest = read_json(folder / "manifest.json")
        board = manifest.get("board") or {}
        for name in ("平面图.dxf", "平面图.png"):
            shutil.copyfile(folder / selected / name, stage / name)
        project = parse(read_json(folder / "project.json"))
        layout = read_json(folder / selected / "layout.json")
        alignment = verify_alignment(project, layout["placements"], stage / "平面图.dxf")
        write_json(stage / "几何对齐.json", alignment)
        if board.get("png"):
            shutil.copyfile(folder / board["png"], stage / "方案比选.png")
        data = read_json(stage / "整区物件.json")
        info = write_delivery(stage, data, ranking, selected, answer.get("rendering"), bool(option))
        if out.exists():
            raise InputError(f"输出路径已被其他任务使用：{out}")
        stage.rename(out)
    return {"out": str(out), "status": "ready", **info}


def procure(folder, option, out):
    from procurement import schedule
    from procurement_view import write_locator

    if out.resolve().is_relative_to(folder.resolve()):
        raise InputError("采购目录须位于原方案包之外")
    verify(folder)
    manifest = read_json(folder / "manifest.json")
    if option not in {row["id"] for row in manifest["options"]}:
        raise InputError(f"方案 {option} 未通过校核或不存在")
    project = parse(read_json(folder / "project.json"))
    source = folder / option
    layout = read_json(source / "layout.json")
    handoff = schedule(project, layout)
    handoff["layout_sha256"] = layout["layout_sha256"]
    handoff["project_sha256"] = layout["project_sha256"]
    out = new_path(out)
    kinds = {"furniture": "成品与家具", "wall": "墙面定制", "ceiling": "顶面安装", "floor": "地面饰面"}
    dimensions = {"span_mm": "跨度", "length_mm": "长", "width_mm": "宽", "depth_mm": "深",
                  "height_mm": "高", "bottom_mm": "底标高", "top_mm": "顶标高", "base_mm": "底标高",
                  "face_span_mm": "面宽", "thickness_envelope_mm": "厚度", "run_mm": "连续灯长"}

    def specs(item):
        return "；".join(f"{dimensions.get(key, key)} {value:g} mm" for key, value in item["dimensions_mm"].items())

    def table(path, headers, rows):
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            for row in rows:
                writer.writerow(["'" + cell if isinstance(cell, str) and cell.lstrip().startswith(("=", "+", "-", "@"))
                                 else cell for cell in row])

    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".opc-procure-") as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir()
        write_json(stage / "采购交底.json", handoff)
        table(stage / "采购清单.csv", ["采购号", "类别", "名称", "数量", "单位", "尺寸", "选型要求", "下单前核对", "位置编号"],
              [(item["id"], kinds[item["category"]], item["name"], item["quantity"], item["unit"], specs(item),
                item["spec"], item["preorder_check"], "、".join(item["locator_ids"])) for item in handoff["items"]])
        table(stage / "安装定位.csv", ["位置编号", "采购号", "名称", "团队区", "布置组序号", "中心 X mm", "中心 Y mm",
                                        "底标高 mm", "顶标高 mm", "旋转 °"],
              [(item["id"], item["item_id"], item["name"], item.get("group_label"), item.get("placement_index"), *item["center_mm"],
                item["base_mm"], item["top_mm"], item.get("rotation_deg")) for item in handoff["locators"]])
        table(stage / "配套复核.csv", ["待核项", "涉及内容", "现场需确认"],
              [(row["topic"], row["applies_to"], row["check"]) for row in handoff["interfaces"]])
        components = [(item["id"], item["name"], part["name"], part["quantity"], part["unit"], specs(part),
                       item.get("components_note", "")) for item in handoff["items"] for part in item.get("components", [])]
        if components:
            table(stage / "定制拆分.csv", ["采购号", "整组名称", "部件", "组内数量", "单位", "模型尺寸", "加工说明"], components)
        write_locator(stage, project, layout, handoff, source / "平面图.dxf")
        files = sorted(file.name for file in stage.iterdir() if file.is_file())
        if out.exists():
            raise InputError(f"输出路径已被其他任务使用：{out}")
        stage.rename(out)
    return {"out": str(out), "status": "ready_for_quote", "option": option, "items": len(handoff["items"]),
            "locators": len(handoff["locators"]), "files": files}


def presentation_arguments(command):
    command.add_argument("--brief", action="store_true", help="一页比选及图纸 ZIP，省略多页报告")
    command.add_argument("--quality", choices=("quick", "studio"), default="quick",
                         help="本地快速轴测，或使用已安装的 Blender 精渲染")
    command.add_argument("--blender", type=Path, help="Blender 可执行文件路径，供 studio 使用")


def parser():
    command = argparse.ArgumentParser(description="OPC 空间设计。几何、绘图和方案导出")
    sub = command.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect", help="检查输入文件及图层，或生成图像预览")
    p.add_argument("source", type=Path)
    p.add_argument("--preview", type=Path)
    p.add_argument("--page", type=int, default=1)
    p = sub.add_parser("import-dxf", help="将已映射图层转换成待确认的毫米几何")
    p.add_argument("source", type=Path)
    p.add_argument("--seed", type=Path, required=True)
    p.add_argument("--layers", type=Path)
    p.add_argument("--unit", choices=NAMED_UNITS)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("calibrate", help="用已确认尺寸将像素描图换算为毫米")
    p.add_argument("project", type=Path)
    p.add_argument("--calibration", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("preview", help="绘制待确认的净使用轮廓和固定结构")
    p.add_argument("project", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("analyze", help="求解并复核，不生成方案文件")
    p.add_argument("project", type=Path)
    p = sub.add_parser("generate", help="生成可核对的图纸、效果图、预算和方案包")
    p.add_argument("project", type=Path)
    p.add_argument("--out", type=Path, required=True)
    presentation_arguments(p)
    p.add_argument("--layouts", type=Path, help="直接校核已布置的候选，再导出方案")
    p = sub.add_parser("revise", help="基于选定方案重新求解和导出")
    p.add_argument("bundle", type=Path)
    p.add_argument("--option", choices="ABC", required=True)
    p.add_argument("--changes", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    presentation_arguments(p)
    p = sub.add_parser("verify", help="复核文件摘要、项目版本、布局及预算")
    p.add_argument("bundle", type=Path)
    p = sub.add_parser("recommend", help="按设计优先项比较并推荐已验证方案")
    p.add_argument("bundle", type=Path)
    p.add_argument("--priorities", nargs="+", choices=PRIORITIES)
    p = sub.add_parser("suggest", help="按场地、风格、用途和条件推荐物件及装饰")
    p.add_argument("brief", type=Path)
    p = sub.add_parser("office-services", help="按办公人数、工区和现场条件规划日常配套")
    p.add_argument("project", type=Path)
    p.add_argument("--brief", type=Path, help="峰值人数、用途、现场条件及保留配套")
    p = sub.add_parser("overview", help="生成选定方案的整区图和逐件查看页")
    p.add_argument("bundle", type=Path)
    p.add_argument("--option", choices="ABC", required=True)
    p.add_argument("--brief", type=Path, help="整区用途、分区、物件和饰面图案对应")
    p.add_argument("--quality", choices=("quick", "studio"), default="quick",
                   help="studio 额外输出同坐标的带纹样 Blender 渲染")
    p.add_argument("--blender", type=Path, help="Blender 可执行文件路径")
    p.add_argument("--azimuth", type=float, help="贴图三维方位角，用于同底图对照")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("deliver", help="交付推荐方案、平面、三维和逐件摆放资料")
    p.add_argument("bundle", type=Path)
    p.add_argument("--option", choices="ABC", help="明确选择已经通过校核的方案")
    p.add_argument("--brief", type=Path, help="整区说明 JSON")
    p.add_argument("--quality", choices=("quick", "studio"), default="quick")
    p.add_argument("--blender", type=Path)
    p.add_argument("--azimuth", type=float)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("procure", help="从选定方案导出采购、安装定位与现场复核清单")
    p.add_argument("bundle", type=Path)
    p.add_argument("--option", choices="ABC", required=True)
    p.add_argument("--out", type=Path, required=True)
    return command


def run(args):
    name = args.command
    if name == "inspect":
        return inspect(args.source, new_path(args.preview) if args.preview else None, args.page)
    if name == "import-dxf":
        raw, info = import_dxf(args.source, read_json(args.seed),
                               read_json(args.layers) if args.layers else None, args.unit)
        write_json(new_path(args.out), raw)
        return {"out": str(args.out), "status": "needs_confirmation", **info}
    if name == "calibrate":
        raw = calibrate(read_json(args.project), read_json(args.calibration))
        write_json(new_path(args.out), raw)
        return {"out": str(args.out), "status": "needs_confirmation"}
    if name == "preview":
        from visual import plan_image
        plan_image(parse(read_json(args.project), require_confirmed=False), [], "几何核对").save(new_path(args.out))
        return {"out": str(args.out)}
    if name == "verify":
        return verify(args.bundle)
    if name == "recommend":
        return recommend(args.bundle, args.priorities)
    if name == "suggest":
        from style_suggest import suggest
        return suggest(read_json(args.brief))
    if name == "office-services":
        from office_services import office_services
        project = parse(read_json(args.project), require_confirmed=False)
        return office_services(project, read_json(args.brief) if args.brief else None)
    if name in ("overview", "deliver"):
        if args.azimuth is not None and not 0 <= args.azimuth < 360:
            raise InputError("贴图三维方位角须为 0 至小于 360 的角度")
        action = deliver if name == "deliver" else overview
        return action(args.bundle, args.option, args.out, read_json(args.brief) if args.brief else None,
                      args.brief.resolve().parent if args.brief else None, quality=args.quality,
                      blender=args.blender, azimuth=args.azimuth)
    if name == "procure":
        return procure(args.bundle, args.option, args.out)
    if name == "revise":
        return revise(args.bundle, args.option, read_json(args.changes), args.out,
                      brief=args.brief, quality=args.quality, blender=args.blender)
    project = parse(read_json(args.project))
    if name == "analyze":
        solutions, stats = search(project)
        options = [{"id": chr(65 + i), "strategy": strategy, "placements": positions, "audit": audit}
                   for i, (strategy, positions, audit) in enumerate(solutions)]
        return {"status": status_for(solutions, project.req["options"]),
                "solutions": [{**{k: v for k, v in o.items() if k != "audit"}, **o["audit"]} for o in options],
                "search": stats, "recommendation": rank_options(project, options)}
    submitted = read_json(args.layouts) if args.layouts else None
    if args.layouts and submitted is None:
        raise InputError("布局候选须为列表")
    return output_bundle(project, args.out, brief=args.brief, quality=args.quality, blender=args.blender,
                         submitted=submitted)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    try:
        result = run(args)
    except (InputError, OSError, KeyError, TypeError, json.JSONDecodeError, BadZipFile) as exc:
        print(json.dumps({"status": "input_error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 3 if args.command not in ("verify", "recommend") and result.get("status") == "no_layout" else 0


if __name__ == "__main__":
    raise SystemExit(main())
