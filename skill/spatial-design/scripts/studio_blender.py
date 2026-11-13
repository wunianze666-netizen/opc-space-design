"""Blender-side worker for dimensionally consistent presentation renders."""

import json
import sys
from collections import defaultdict
from math import cos, radians, sin, sqrt
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


GLASS = (163, 196, 201)
BACKDROP = (211, 219, 219)


def srgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def linear(channel):
    value = channel / 255
    return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4


def math_socket(nodes, links, operation, first, second=None):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    for index, value in enumerate((first, second)):
        if value is None:
            continue
        if isinstance(value, (int, float)):
            node.inputs[index].default_value = value
        else:
            links.new(value, node.inputs[index])
    return node.outputs[0]


def surface_texture(material, shader, base, finish, pattern):
    nodes, links = material.node_tree.nodes, material.node_tree.links
    position = nodes.new("ShaderNodeNewGeometry").outputs["Position"]
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Detail"].default_value = 2.3
    is_wood = finish == "wood" or (finish == "floor" and pattern in ("wood", "light_oak"))
    if is_wood:
        grain = nodes.new("ShaderNodeVectorMath")
        grain.operation = "MULTIPLY"
        grain.inputs[1].default_value = (1.5, 74, 1.5)
        links.new(position, grain.inputs[0])
        links.new(grain.outputs["Vector"], noise.inputs["Vector"])
        noise.inputs["Scale"].default_value = 2.2
    else:
        links.new(position, noise.inputs["Vector"])
        noise.inputs["Scale"].default_value = 125 if finish == "fabric" else 8.5
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = .19
    ramp.color_ramp.elements[1].position = .81
    contrast = .13 if is_wood else .075
    ramp.color_ramp.elements[0].color = (*(value * (1 - contrast) for value in base), 1)
    ramp.color_ramp.elements[1].color = (*(min(1, value * (1 + contrast)) for value in base), 1)
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    paint = ramp.outputs["Color"]
    if finish == "floor":
        split = nodes.new("ShaderNodeSeparateXYZ")
        links.new(position, split.inputs["Vector"])
        x, y = split.outputs["X"], split.outputs["Y"]
        if is_wood:
            board_width, board_length = (.185, 1.8) if pattern == "light_oak" else (.26, 1.62)
        else:
            board_width = board_length = 2.7 if pattern == "concrete" else 1.1
        row = math_socket(nodes, links, "DIVIDE", y, board_width)
        column = math_socket(nodes, links, "DIVIDE", x, board_length)
        if is_wood:
            stagger = math_socket(nodes, links, "MULTIPLY", math_socket(nodes, links, "FLOOR", row), .33)
            column = math_socket(nodes, links, "ADD", column, stagger)
        seam_x = math_socket(nodes, links, "LESS_THAN", math_socket(nodes, links, "FRACT", column), .004 / board_length)
        seam_y = math_socket(nodes, links, "LESS_THAN", math_socket(nodes, links, "FRACT", row), .004 / board_width)
        seam = math_socket(nodes, links, "MAXIMUM", seam_x, seam_y)
        blend = nodes.new("ShaderNodeMixRGB")
        links.new(math_socket(nodes, links, "MULTIPLY", seam, .45), blend.inputs["Fac"])
        links.new(paint, blend.inputs[1])
        blend.inputs[2].default_value = (*(value * .68 for value in base), 1)
        paint = blend.outputs["Color"]
        if pattern == "terrazzo":
            chips = nodes.new("ShaderNodeTexVoronoi")
            chips.inputs["Scale"].default_value = 88
            links.new(position, chips.inputs["Vector"])
            flecks = nodes.new("ShaderNodeMixRGB")
            links.new(math_socket(nodes, links, "LESS_THAN", chips.outputs["Distance"], .23), flecks.inputs["Fac"])
            links.new(paint, flecks.inputs[1])
            flecks.inputs[2].default_value = (*(value * .59 for value in base), 1)
            paint = flecks.outputs["Color"]
    links.new(paint, shader.inputs["Base Color"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = .14 if is_wood else .09
    bump.inputs["Distance"].default_value = .0007 if finish == "fabric" else .0017
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])


def material(color, finish, pattern):
    obj = bpy.data.materials.new(f"{finish} #{''.join(f'{n:02x}' for n in color)}")
    obj.use_nodes = True
    shader = obj.node_tree.nodes.get("Principled BSDF")
    base = tuple(map(linear, color))
    shader.inputs["Base Color"].default_value = (*base, 1.0)
    obj.diffuse_color = (*base, 1.0)
    shader.inputs["Roughness"].default_value = {
        "floor": .58 if pattern != "stone" else .37, "wood": .48,
        "fabric": .79, "glass": .16, "backdrop": .82,
    }.get(finish, .57)
    if finish == "glass":
        transmission = shader.inputs.get("Transmission Weight")
        if transmission is None:
            transmission = shader.inputs.get("Transmission")
        if transmission is not None:
            transmission.default_value = .78
        shader.inputs["IOR"].default_value = 1.42
    elif finish == "emitter":
        emission = shader.inputs.get("Emission Color")
        if emission is None:
            emission = shader.inputs.get("Emission")
        if emission is not None:
            emission.default_value = (*base, 1.0)
        shader.inputs["Emission Strength"].default_value = 2.1
    elif finish in ("floor", "wood", "fabric"):
        surface_texture(obj, shader, base, finish, pattern)
    return obj


def local_point(point, origin):
    x0, y0 = origin
    x, y, z = point
    return (x - x0) / 1000, (y - y0) / 1000, z / 1000


def local_faces(faces, origin):
    return [([local_point(point, origin) for point in vertices], tuple(color))
            for vertices, color in faces]


def build_model(faces, palette, interior=None):
    floor = srgb(palette["floor"])
    timber = srgb(palette["desk"])
    fabric = {srgb(palette["chair"]), srgb(palette["soft"])}
    pattern = palette.get("floor_pattern", "wood")
    materials = interior["materials"] if interior else {}
    floor_finishes = {srgb(materials[name]): {"oak": "light_oak", "walnut": "wood",
                                             "terrazzo": "terrazzo"}.get(name, "stone")
                      for name in interior["floor_materials"]} if interior else {}
    wood_finishes = {srgb(materials[name]) for name in ("oak", "walnut") if name in materials}
    acoustic_finish = srgb(materials["sage"]) if "sage" in materials else None
    emitter = srgb(materials["warm_light"]) if "warm_light" in materials else None
    groups = defaultdict(list)
    for xyz, color in faces:
        chosen_pattern = pattern
        if (color == floor or color in floor_finishes) and all(p[2] == 0 for p in xyz):
            finish, chosen_pattern = "floor", floor_finishes.get(color, pattern)
        elif color == GLASS:
            finish = "glass"
        elif color == emitter:
            finish = "emitter"
        elif color == timber or color in wood_finishes:
            finish = "wood"
        elif color in fabric or color == acoustic_finish:
            finish = "fabric"
        else:
            finish = "solid"
        groups[color, finish, chosen_pattern].append(xyz)
    for (color, finish, chosen_pattern), polygons in groups.items():
        vertices, vertex_ids, indices = [], {}, []
        for polygon in polygons:
            face = []
            for point in polygon:
                if point not in vertex_ids:
                    vertex_ids[point] = len(vertices)
                    vertices.append(point)
                face.append(vertex_ids[point])
            indices.append(face)
        mesh = bpy.data.meshes.new("Layout geometry")
        mesh.from_pydata(vertices, [], indices)
        mesh.update()
        if len(mesh.polygons) != len(polygons):
            raise ValueError("模型表面数量与布局数据不一致。")
        obj = bpy.data.objects.new(finish, mesh)
        bpy.context.collection.objects.link(obj)
        obj.data.materials.append(material(color, finish, chosen_pattern))


def build_patterns(option, textures, destination, origin):
    for ref, faces in option.get("pattern_faces", {}).items():
        spec = textures[ref]
        vertices, polygons = [], []
        for face in faces:
            polygons.append(tuple(range(len(vertices), len(vertices) + len(face["xyz"]))))
            vertices.extend(local_point(point, origin) for point in face["xyz"])
        mesh = bpy.data.meshes.new(f"Pattern {ref}")
        mesh.from_pydata(vertices, [], polygons)
        mesh.update()
        layer = mesh.uv_layers.new(name="PatternUV")
        for polygon, face in zip(mesh.polygons, faces, strict=True):
            for index, coords in zip(polygon.loop_indices, face["uv"], strict=True):
                layer.data[index].uv = coords

        obj = bpy.data.objects.new(f"Pattern {ref}", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj["opc_ref"] = ref
        obj["opc_repeat_mm"] = spec["repeat_mm"]
        obj["opc_extent_mm"] = spec["extent_mm"]
        obj["opc_mode"] = spec["mode"]
        obj["opc_origin_mm"] = origin
        obj["opc_asset_sha256"] = spec["asset_sha256"]
        mat = material(faces[0]["color"], "solid", "wood")
        mat.name = f"Surface {ref}"
        obj.data.materials.append(mat)
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        shader = nodes.get("Principled BSDF")
        shader.inputs["Roughness"].default_value = .86
        specular = shader.inputs.get("Specular IOR Level") or shader.inputs.get("Specular")
        if specular is not None:
            specular.default_value = .22
        coordinates = nodes.new("ShaderNodeUVMap")
        coordinates.uv_map = "PatternUV"
        texture = nodes.new("ShaderNodeTexImage")
        texture.name = "OPC surface swatch"
        texture.extension = "REPEAT" if spec["mode"] == "repeat" else "EXTEND"
        texture.image = bpy.data.images.load(str(destination / spec["asset"]), check_existing=True)
        if not texture.image.packed_file:
            texture.image.pack()
        texture.image.filepath = "//" + spec["asset"]
        links.new(coordinates.outputs["UV"], texture.inputs["Vector"])
        if texture.image.depth == 32:
            mix = nodes.new("ShaderNodeMixRGB")
            mix.inputs[1].default_value = shader.inputs["Base Color"].default_value
            links.new(texture.outputs["Alpha"], mix.inputs["Fac"])
            links.new(texture.outputs["Color"], mix.inputs[2])
            links.new(mix.outputs["Color"], shader.inputs["Base Color"])
        else:
            links.new(texture.outputs["Color"], shader.inputs["Base Color"])


def light(name, position, target, energy, size, color):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.size, data.color = energy, size, color
    data.shape = "DISK"
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = position
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()
    return data


def common_camera(options, settings):
    azimuth = radians(settings["azimuth_degrees"])
    elevation = radians(settings["elevation_degrees"])
    direction = Vector((cos(azimuth) * cos(elevation), sin(azimuth) * cos(elevation), sin(elevation)))
    rotation = (-direction).to_track_quat("-Z", "Y")
    inverse = rotation.inverted()
    points = [Vector(point) for option in options for xyz, _ in option["faces"] for point in xyz]
    points.extend(Vector(point) for option in options for faces in option.get("pattern_faces", {}).values()
                  for face in faces for point in face["local_xyz"])
    if not points:
        raise ValueError("没有可渲染的布局表面。")
    projected = [inverse @ point for point in points]
    x0, y0, z0 = (min(point[i] for point in projected) for i in range(3))
    x1, y1, z1 = (max(point[i] for point in projected) for i in range(3))
    width, height = settings["resolution_px"]
    scale = max(x1 - x0, (y1 - y0) * width / height) / .82
    span = max(scale, z1 - z0, 1)
    center = rotation @ Vector(((x1 + x0) / 2, (y1 + y0) / 2, (z1 + z0) / 2))
    return {"rotation": rotation, "position": center + direction * (2 * span + 4),
            "scale": scale, "span": span, "points": points}


def prepare_scene(path, settings, *, inside=False, keep_cutaway=False):
    if keep_cutaway:
        scene = bpy.data.scenes.new("Eye level")
        bpy.context.window.scene = scene
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.name = "Eye level" if inside else "Cutaway"
    scene.unit_settings.system = "METRIC"
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = min(settings["samples"], 22) if inside else settings["samples"]
    scene.cycles.adaptive_threshold = .065 if inside else .035
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 4 if settings["samples"] <= 16 else 5 if inside else 6
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 6
    scene.render.resolution_x, scene.render.resolution_y = settings["resolution_px"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.filepath = str(path)
    try:
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        scene.view_settings.view_transform = "Filmic"
    world = bpy.data.worlds.new("Soft environment")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs[0].default_value = (.83, .88, 1.0, 1)
    background.inputs[1].default_value = (.35 if keep_cutaway else .65) if inside else .46
    scene.world = world
    return scene


def render(option, palette, camera, settings, destination, interior=None, textures=None, origin=None):
    scene = prepare_scene(destination / (option["id"] + ".png"), settings)
    if origin is not None:
        scene["opc_origin_mm"] = origin
    build_model(option["faces"], palette, interior)
    build_patterns(option, textures, destination, origin)

    span = camera["span"]
    bpy.ops.mesh.primitive_plane_add(size=span * 12, location=(0, 0, -.173))
    bpy.context.object.name = "Presentation backdrop"
    bpy.context.object.data.materials.append(material(BACKDROP, "backdrop", "wood"))
    target = Vector((0, 0, 0))
    energy = span ** 2
    light("Warm key", Vector((-.38, -.27, .71)) * span, target, 21 * energy, .55 * span, (1, .94, .84))
    light("Cool fill", Vector((.49, .22, .58)) * span, target, 9 * energy, .57 * span, (.82, .91, 1))
    light("Edge light", Vector((-.09, .49, .47)) * span, target, 7 * energy, .43 * span, (1, .98, .93))

    data = bpy.data.cameras.new("Comparison camera")
    cam = bpy.data.objects.new(data.name, data)
    bpy.context.collection.objects.link(cam)
    cam.location = camera["position"]
    cam.rotation_euler = camera["rotation"].to_euler()
    data.type = "ORTHO"
    data.sensor_fit = "HORIZONTAL"
    data.ortho_scale = camera["scale"]
    data.clip_start = .02
    data.clip_end = 5 * span + 100
    scene.camera = cam
    bpy.context.view_layer.update()
    projected = [world_to_camera_view(scene, cam, point) for point in camera["points"]]
    margin = min(min(point.x, 1 - point.x, point.y, 1 - point.y) for point in projected)
    if margin < .075 or any(not data.clip_start < point.z < data.clip_end for point in projected):
        raise ValueError("模型未能完整纳入精渲染画面。")
    bpy.ops.render.render(write_still=True)
    return margin


def render_eye(option, palette, settings, destination, eye, interior, origin, textures=None, keep_cutaway=False):
    scene = prepare_scene(destination / (option["id"] + "-eye.png"), settings,
                          inside=True, keep_cutaway=keep_cutaway)
    scene["opc_origin_mm"] = origin
    build_model(option["faces"], palette, interior)
    build_patterns(option, textures, destination, origin)
    view = settings["eye_view"]
    position = Vector(local_point(eye["eye_mm"], origin))
    target = Vector(local_point(eye["target_mm"], origin))
    light_scale = .38 if keep_cutaway else 1
    for index, item in enumerate(interior["lights"]):
        source = Vector(local_point(item["position_mm"], origin))
        width, depth = (dimension / 1000 for dimension in item["size_mm"])
        data = light(f"Ceiling light {index + 1}", source, source - Vector((0, 0, 1)),
                     light_scale * min(450, max(100, width * depth * 160)), width, (1, .88, .73))
        data.shape = "RECTANGLE"
        data.size_y = max(.04, depth)

    ceiling = settings["ceiling_mm"] / 1000
    light_height = ceiling - .18
    span = max(1, sqrt(interior["floor_area_m2"]))
    for index, point in enumerate((position, target)):
        source = Vector((point.x, point.y, light_height))
        aim = Vector((point.x, point.y, max(.2, light_height - 1.4)))
        if (source - aim).length < .05:
            continue
        light(f"Soft room fill {index + 1}", source, aim, light_scale * min(1250, 120 + span * 35),
              min(3.3, max(.7, span * .18)), (1, .96, .90) if index == 0 else (.81, .91, 1))
    scene.view_settings.exposure = -.22 if keep_cutaway else .22
    data = bpy.data.cameras.new("Eye-level camera")
    cam = bpy.data.objects.new(data.name, data)
    bpy.context.collection.objects.link(cam)
    cam.location = position
    cam.rotation_euler = (target - position).to_track_quat("-Z", "Y").to_euler()
    data.lens = view["lens_mm"]
    data.sensor_width = 36
    data.clip_start = .035
    data.clip_end = 5 * span + 100
    scene.camera = cam
    bpy.ops.render.render(write_still=True)


def main():
    source, destination = map(Path, sys.argv[sys.argv.index("--") + 1:])
    specification = json.loads(source.read_text(encoding="utf-8"))
    settings = specification["settings"]
    origin = specification["origin_mm"]
    interior = specification.get("interior")
    textures = specification.get("textures", {})
    save_model = specification.get("save_model", False)

    def local_option(item):
        patterns = {ref: [{**face, "local_xyz": [local_point(point, origin) for point in face["xyz"]]}
                          for face in faces] for ref, faces in item.get("pattern_faces", {}).items()}
        return {"id": item["id"], "faces": local_faces(item["faces"], origin), "pattern_faces": patterns}

    options = [local_option(item) for item in specification["options"]]
    camera = common_camera(options, settings)
    palette = specification["palette"]
    margins = [render(option, palette, camera, settings, destination, interior, textures, origin) for option in options]
    report = {"ids": [option["id"] for option in options], "blender_version": bpy.app.version_string,
              "ortho_scale_m": round(camera["scale"], 4), "frame_margin_fraction": round(min(margins), 4)}
    if "eye" in specification:
        eye = specification["eye"]
        report["eye_ids"], report["eye_errors"] = [], {}
        for item in eye["options"]:
            option = local_option(item)
            try:
                render_eye(option, palette, settings, destination, eye["view"], interior, origin,
                           textures, keep_cutaway=save_model)
            except Exception as exc:
                report["eye_errors"][option["id"]] = f"人视渲染失败。{str(exc)[:180]}"
            else:
                report["eye_ids"].append(option["id"])
    if save_model:
        for scene in bpy.data.scenes:
            scene.render.filepath = "//" + ("墙地顶人视.png" if scene.name == "Eye level" else "纹样三维.png")
        bpy.ops.wm.save_as_mainfile(filepath=str(destination / "model.blend"))
    (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"OPC_RENDER_ERROR: {exc}", file=sys.stderr, flush=True)
        raise
