"""Render validated layout geometry through an optional local Blender."""

import json
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from math import atan2, cos, degrees, hypot, radians, sin
from pathlib import Path

from PIL import Image
from shapely import constrained_delaunay_triangles, union_all
from shapely.errors import GEOSException
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient

from layout import pieces
from model import InputError, digest
from visual import EXTERIOR_CUT_MM, EXTERIOR_WALL_MM, PALETTES, SLAB_MM, piece_corners, rgb, scene, scene_height


WIDTH = 1600
HEIGHT = 1040
SAMPLES = 32
ELEVATION = 39
SECTION_MM = 1450
RENDER_NAME = "精渲染.png"
EYE_NAME = "人视效果.png"
EYE_LENS_MM = 28


def blender_executable(requested):
    configured = requested or os.environ.get("OPC_BLENDER")
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve())
    found = shutil.which(str(configured) if configured else "blender")
    return str(Path(found).resolve()) if found else None


def camera_angle(project, options):
    """Choose one comparison angle that shows entry-facing objects."""
    preferred = project.req.get("visitor_entries", [])
    entries = [entry for entry in project.exits if not preferred or entry.name in preferred]
    directions = []
    for option in options:
        for obj in pieces(option["placements"], project):
            weight = {"focus_pod": 1.6, "gallery": 1.3, "whiteboard": .8, "counter": .6}.get(obj["kind"])
            if weight is None:
                continue
            a, _, _, d = piece_corners(obj)
            dx, dy = a - d
            length = hypot(dx, dy)
            if length > 0:
                directions.append((dx / length, dy / length, weight))
    if entries:
        angles = []
        for entry in entries:
            normal = atan2(-entry.normal[1], -entry.normal[0])
            angles.extend(normal + radians(offset) for offset in (36, -36, 51, -51))
    else:
        angles = list(map(radians, (225, 45, 135, 315)))

    def score(angle):
        x, y = cos(angle), sin(angle)
        entrance = max((-entry.normal[0] * x - entry.normal[1] * y for entry in entries), default=0)
        weight_total = sum(weight for _, _, weight in directions)
        faces = (sum(max(0, dx * x + dy * y) * weight for dx, dy, weight in directions) /
                 weight_total if weight_total else 0)
        return .65 * entrance + .35 * faces

    return max(angles, key=score)


def join_wall_faces(faces, paint, owners=None):
    """Remove coincident surfaces where exterior wall strips overlap at corners."""
    groups = defaultdict(list)
    result = []
    for index, (points, color) in enumerate(faces):
        exterior = owners is None or (owners[index] and owners[index][0] in ("exterior_wall", "exit_header"))
        if color != paint or not exterior:
            result.append((points, color))
            continue
        x, y, z = points[0]
        if all(point[2] == z for point in points):
            key = ("horizontal", z)
            groups[key].append(points)
            continue
        u, v, _ = points[1]
        length = hypot(u - x, v - y)
        if not length:
            result.append((points, color))
            continue
        dx, dy = (u - x) / length, (v - y) / length
        if dx < -1e-9 or abs(dx) <= 1e-9 and dy < 0:
            dx, dy = -dx, -dy
        normal = round(-dy * x + dx * y, 6)
        key = ("vertical", round(dx, 10), round(dy, 10), normal)
        groups[key].append(points)

    for key, originals in groups.items():
        if len(originals) == 1:
            result.append((originals[0], paint))
            continue
        if key[0] == "horizontal":
            z = key[1]
            polygons = [Polygon([(x, y) for x, y, _ in points]) for points in originals]
            lift = lambda u, v: (u, v, z)
        else:
            _, dx, dy, normal = key
            polygons = [Polygon([(dx * x + dy * y, z) for x, y, z in points]) for points in originals]
            lift = lambda u, v: (dx * u - dy * normal, dy * u + dx * normal, v)
        merged = union_all(polygons)
        if sum(poly.area for poly in polygons) - merged.area < 1e-5:
            result.extend((points, paint) for points in originals)
            continue
        sign = 1 if polygons[0].exterior.is_ccw else -1
        for triangle in constrained_delaunay_triangles(merged).geoms:
            ring = list(orient(triangle, sign=sign).exterior.coords)[:-1]
            result.append(([lift(u, v) for u, v in ring], paint))
    return result


def studio_geometry(project, placements, angle, *, eye=False):
    """Return the geometry and material colors sent to Blender, in project millimeters."""
    owners = []
    faces = scene(project, placements, angle, section_height=None if eye else SECTION_MM,
                  cover_ceiling=eye, owners=owners)
    return join_wall_faces(faces, rgb(PALETTES[project.req["style"]]["wall"]), owners)


def texture_projectors(project, patterns):
    elements = (project.req.get("interior") or {}).get("elements", [])
    projectors = {}
    for pattern in patterns:
        index = int(pattern["ref"][1:]) - 1
        element = elements[index]
        width, height = pattern["repeat_mm"]
        shape = element["footprint"]
        if element["kind"] == "floor_finish":
            x0, _, _, y1 = shape.bounds
            projectors[index] = {"origin": (x0, y1, 0), "u": (1 / width, 0, 0),
                                 "v": (0, 1 / height, 0)}
        elif element["kind"] == "art":
            a, b = element["segment"]
            length = hypot(b[0] - a[0], b[1] - a[1])
            tx, ty = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            cx, cy = shape.centroid.coords[0]
            nx, ny = -ty, tx
            start = a
            if (cx - a[0]) * nx + (cy - a[1]) * ny > 0:
                tx, ty, start = -tx, -ty, b
            else:
                nx, ny = -nx, -ny
            depth = max((x - a[0]) * nx + (y - a[1]) * ny for x, y in shape.exterior.coords)
            projectors[index] = {"origin": (start[0] + depth * nx, start[1] + depth * ny, element["top_mm"]),
                                 "u": (tx / width, ty / width, 0), "v": (0, 0, 1 / height),
                                 "plane": (nx, ny, 0)}
        else:
            raise InputError(f"{pattern['ref']} 三维纹样须对应平面墙艺 art 或地面 floor_finish")
    return projectors


def textured_geometry(project, option, angle, projectors, *, eye=False):
    owners = []
    faces = scene(project, option["placements"], angle, section_height=None if eye else SECTION_MM,
                  cover_ceiling=eye, owners=owners, textured_indices=projectors)
    ordinary, ordinary_owners = [], []
    patterns = defaultdict(list)
    for (vertices, color), owner in zip(faces, owners, strict=True):
        index = owner[1] if owner and owner[0] == "interior" else None
        projector = projectors.get(index)
        if projector:
            offsets = [tuple(value - base for value, base in zip(point, projector["origin"], strict=True))
                       for point in vertices]
            plane = projector.get("plane", (0, 0, 1))
            if all(abs(sum(a * b for a, b in zip(point, plane, strict=True))) < .001 for point in offsets):
                uv = [[sum(a * b for a, b in zip(point, projector["u"], strict=True)),
                       1 + sum(a * b for a, b in zip(point, projector["v"], strict=True))] for point in offsets]
                patterns[f"D{index+1:03d}"].append({"xyz": vertices, "uv": uv, "color": color})
                continue
        ordinary.append((vertices, color))
        ordinary_owners.append(owner)
    plain = join_wall_faces(ordinary, rgb(PALETTES[project.req["style"]]["wall"]), ordinary_owners)
    if eye and len(patterns) != len(projectors):
        raise InputError("至少一处指定的纹样没有匹配到实际三维表面")
    return {"id": option["id"], "faces": plain, "pattern_faces": dict(patterns)}


def studio_scene_inputs(project, options):
    """Derive the common camera angle, per-option faces and exact input digests."""
    angle = camera_angle(project, options)
    scenes = [{"id": option["id"], "faces": studio_geometry(project, option["placements"], angle)}
              for option in options]
    hashes = {entry["id"]: digest(entry["faces"]) for entry in scenes}
    return angle, scenes, hashes


def eye_scene_inputs(project, options):
    """Return exact interior camera pose, renderable option geometry, and pose-bound digests."""
    interior = project.req.get("interior")
    view = interior["view"] if interior else None
    if view is None:
        return None, [], {}
    x, y, z = view["eye_mm"]
    eye = Point(x, y)
    scenes = []
    for option in options:
        if any(item["base"] <= z <= item["base"] + item["height"] and Polygon(item["polygon"]).covers(eye)
               for item in pieces(option["placements"], project)):
            continue
        scenes.append({"id": option["id"], "faces": studio_geometry(project, option["placements"], None, eye=True)})
    hashes = {entry["id"]: digest({"faces": entry["faces"], "view": view, "lens_mm": EYE_LENS_MM})
              for entry in scenes}
    return view, scenes, hashes


def interior_render_settings(project):
    interior = project.req.get("interior")
    if not interior:
        return None
    lights = []
    floor_materials = set()
    for element in interior["elements"]:
        kind, shape = element["kind"], element["footprint"]
        if kind == "floor_finish":
            floor_materials.add(element["material"])
        elif kind == "ceiling_light":
            x0, y0, x1, y1 = shape.bounds
            x, y = shape.centroid.coords[0]
            lights.append({"position_mm": [x, y, element["bottom_mm"] - 4],
                           "size_mm": [x1 - x0, y1 - y0]})
    return {"materials": interior["materials"], "floor_materials": sorted(floor_materials), "lights": lights,
            "floor_area_m2": project.outline.area / 1e6}


def render_options(root, project, options, blender=None, *, patterns=(), swatches=None,
                   azimuth=None, model_name=None):
    """Publish complete renders together. The caller supplies a new bundle directory."""
    root = Path(root).resolve()
    settings = {"resolution_px": [WIDTH, HEIGHT], "samples": 16 if model_name else SAMPLES,
                "elevation_degrees": ELEVATION,
                "interior_wall_section_mm": SECTION_MM,
                "exterior_wall_thickness_schematic_mm": EXTERIOR_WALL_MM,
                "exterior_wall_section_schematic_mm": EXTERIOR_CUT_MM,
                "slab_thickness_schematic_mm": SLAB_MM,
                "ceiling_mm": scene_height(project), "ceiling_assumed": project.height is None}
    requested_eye = (project.req.get("interior") or {}).get("view")
    if requested_eye:
        settings["eye_view"] = {"pose_mm": requested_eye, "lens_mm": EYE_LENS_MM, "status": "pending",
                                "overhead_geometry": "schematic_flat_plane"}

    def outcome(status, reason=None):
        result = {"status": status, "engine": "Blender Cycles" if status == "rendered" else "local_axon",
                  "view": "orthographic_cutaway", "images": [], "settings": settings}
        if reason:
            result["reason"] = reason
        if requested_eye and status != "rendered":
            settings["eye_view"]["status"] = status
            if reason:
                settings["eye_view"]["reason"] = reason
        return result

    if not options:
        return outcome("not_requested", "当前没有可渲染的布局。")
    executable = blender_executable(blender)
    if executable is None:
        return outcome("fallback", "找不到 Blender。可用 --blender 指定程序路径，或将 Blender 加入 PATH。")

    ids = [option["id"] for option in options]
    if len(ids) > 3 or len(set(ids)) != len(ids) or any(key not in ("A", "B", "C") for key in ids):
        return outcome("fallback", "精渲染需要有效的方案编号。")
    destinations = {key: root / key / RENDER_NAME for key in ids}
    if any(path.exists() or not path.parent.is_dir() or not path.parent.resolve().is_relative_to(root)
           for path in destinations.values()):
        return outcome("fallback", "精渲染目标需要可写入的新方案目录。")

    written = []
    timeout = 720 if model_name else 420 if requested_eye else 240
    try:
        angle, scenes, scene_hashes = studio_scene_inputs(project, options)
        view, eye_scenes, eye_hashes = eye_scene_inputs(project, options)
        if azimuth is not None or swatches:
            angle = radians(azimuth) if azimuth is not None else angle
            projectors = texture_projectors(project, patterns)
            scenes = [textured_geometry(project, option, angle, projectors) for option in options]
            scene_hashes = {entry["id"]: digest(entry) for entry in scenes}
            eye_scenes = [textured_geometry(project, option, None, projectors, eye=True)
                          for option in options if option["id"] in eye_hashes]
            eye_hashes = {entry["id"]: digest({"scene": entry, "view": view, "lens_mm": EYE_LENS_MM})
                          for entry in eye_scenes}
        if view:
            eye_targets = {key: root / key / EYE_NAME for key in eye_hashes}
            if any(path.exists() for path in eye_targets.values()):
                raise ValueError("人视效果图需要可写入的新方案目录。")
        else:
            eye_targets = {}
        settings["azimuth_degrees"] = round(degrees(angle) % 360, 2)
        x0, y0, x1, y1 = project.outline.bounds
        spec = {"settings": settings, "palette": PALETTES[project.req["style"]],
                "origin_mm": [(x0 + x1) / 2, (y0 + y1) / 2], "options": scenes}
        interior_settings = interior_render_settings(project)
        if interior_settings:
            spec["interior"] = interior_settings
        if eye_scenes:
            spec["eye"] = {"view": view, "options": eye_scenes}
        if model_name:
            spec["save_model"] = True
        with tempfile.TemporaryDirectory(prefix=".opc-render-", dir=root.parent) as temporary:
            work = Path(temporary)
            if swatches:
                spec["textures"] = {}
                for pattern in patterns:
                    ref = pattern["ref"]
                    extension, payload = swatches[ref]
                    name = f"{ref}.{extension}"
                    (work / name).write_bytes(payload)
                    spec["textures"][ref] = {key: pattern[key] for key in
                                             ("mode", "repeat_mm", "extent_mm", "asset_sha256")}
                    spec["textures"][ref]["asset"] = name
            source = work / "scene.json"
            source.write_text(json.dumps(spec, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            script = Path(__file__).resolve().with_name("studio_blender.py")
            command = [executable, "--background", "--factory-startup", "-noaudio",
                       "--python-exit-code", "3", "--python", str(script), "--", str(source), str(work)]
            environment = dict(os.environ, TEMP=str(work), TMP=str(work), TMPDIR=str(work))
            result = subprocess.run(command, cwd=work, env=environment, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                detail = next((line.strip() for line in reversed((result.stderr + result.stdout).splitlines())
                               if line.startswith("OPC_RENDER_ERROR:")), "")
                detail = detail.removeprefix("OPC_RENDER_ERROR:").strip()[:240]
                return outcome("fallback", "Blender 精渲染失败。" + (f" {detail}" if detail else ""))
            report = json.loads((work / "report.json").read_text(encoding="utf-8"))
            if report["ids"] != ids:
                raise ValueError("渲染结果与方案编号不一致。")
            eye_ids = report.get("eye_ids", [])
            if len(eye_ids) != len(set(eye_ids)) or any(key not in eye_targets for key in eye_ids):
                raise ValueError("人视渲染结果与方案编号不一致。")
            publications = [(work / f"{key}.png", destinations[key]) for key in ids]
            publications.extend((work / f"{key}-eye.png", eye_targets[key]) for key in eye_ids)
            for image_path, _ in publications:
                with Image.open(image_path) as image:
                    if image.format != "PNG" or image.size != (WIDTH, HEIGHT):
                        raise ValueError("渲染图像尺寸或格式异常。")
                    image.verify()
            if model_name:
                model = work / "model.blend"
                if not model.is_file() or model.stat().st_size < 1024:
                    raise ValueError("Blender 工程文件未成功保存。")
                publications.append((model, root / model_name))
            for source_image, destination in publications:
                source_image.replace(destination)
                written.append(destination)
        settings["blender_version"] = report["blender_version"]
        settings["ortho_scale_m"] = report["ortho_scale_m"]
        settings["frame_margin_fraction"] = report["frame_margin_fraction"]
        if view:
            omitted = {key: "指定相机位置落在该方案的家具范围内。"
                       for key in ids if key not in eye_targets}
            omitted.update(report.get("eye_errors", {}))
            settings["eye_view"]["status"] = "rendered" if len(eye_ids) == len(ids) else (
                "partial" if eye_ids else "fallback")
            if omitted:
                settings["eye_view"]["omitted"] = omitted
        info = outcome("rendered")
        if model_name:
            info["model"] = model_name
        if swatches:
            info["origin_mm"] = spec["origin_mm"]
            info["patterns"] = [{key: pattern[key] for key in
                                 ("ref", "name", "mode", "repeat_mm", "extent_mm", "surface_kind", "asset_sha256")}
                                for pattern in patterns]
        for option in options:
            key = option["id"]
            record = {"id": key, "file": destinations[key].relative_to(root).as_posix(),
                      "scene_sha256": scene_hashes[key]}
            if key in eye_ids:
                record["eye_file"] = eye_targets[key].relative_to(root).as_posix()
                record["eye_scene_sha256"] = eye_hashes[key]
            if option.get("layout_sha256"):
                record["layout_sha256"] = option["layout_sha256"]
            if swatches:
                record["cutaway_surfaces"] = {ref: len(faces) for ref, faces in
                                               next(entry for entry in scenes if entry["id"] == key)["pattern_faces"].items()}
                record["eye_surfaces"] = {ref: len(faces) for ref, faces in
                                           next((entry["pattern_faces"] for entry in eye_scenes if entry["id"] == key), {}).items()}
            info["images"].append(record)
        return info
    except subprocess.TimeoutExpired:
        reason = f"Blender 在 {timeout} 秒内未完成精渲染。"
    except (GEOSException, OSError, ValueError, KeyError) as exc:
        reason = f"无法完成精渲染：{str(exc)[:240]}"
    for path in written:
        path.unlink(missing_ok=True)
    return outcome("fallback", reason)
