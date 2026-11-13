"""Render physically mapped wall and floor textures without external services."""

from io import BytesIO
from math import radians

import numpy as np
from PIL import Image

from studio import textured_geometry, texture_projectors
from visual import axon_frame


def render_cutaway(path, project, layout, patterns, swatches, angle):
    ready = [row for row in patterns if row["ref"] in swatches]
    supported = {"art", "floor_finish"}
    elements = project.req["interior"]["elements"]
    ready = [row for row in ready if elements[int(row["ref"][1:]) - 1]["kind"] in supported]
    if not ready:
        return [], sorted(swatches), angle
    projectors = texture_projectors(project, ready)
    angles = [angle] if angle is not None else [225, 45, 135, 315]
    angle, scene = max(((a, textured_geometry(project, layout, radians(a), projectors)) for a in angles),
                       key=lambda row: len(row[1]["pattern_faces"]))
    faces = scene["faces"]
    owners = [("floor", 0) if all(z == 0 for _, _, z in vertices) else None for vertices, _ in faces]
    modes = {row["ref"]: row["mode"] for row in ready}
    textures = {}
    for ref, surfaces in scene["pattern_faces"].items():
        with Image.open(BytesIO(swatches[ref][1])) as source:
            swatch = source.convert("RGBA")
        paint = Image.new("RGB", swatch.size, tuple(surfaces[0]["color"]))
        paint.paste(swatch, mask=swatch.getchannel("A"))
        raster = np.asarray(paint)
        for surface in surfaces:
            textures[len(faces)] = (raster, np.asarray(surface["uv"]), modes[ref] == "repeat")
            faces.append((surface["xyz"], surface["color"]))
            owners.append(None)
    applied = sorted(scene["pattern_faces"])
    if applied:
        high = axon_frame(project, faces, owners, angle, size=(2250, 1462), textures=textures)
        high.resize((1500, 975), Image.Resampling.LANCZOS).save(path)
    return applied, sorted(set(swatches) - set(applied)), angle
