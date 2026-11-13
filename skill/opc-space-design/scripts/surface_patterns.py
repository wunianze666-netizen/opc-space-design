"""Scale a local material swatch onto a verified interior surface."""

import base64
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageDraw, UnidentifiedImageError
from shapely.geometry import Polygon

from interior import WALL_KINDS
from model import InputError, label, number, object_fields


ASSET_LIMIT = 12 * 1024 * 1024
ASSET_FORMATS = ("PNG", "JPEG", "WEBP")


def load_swatch(root, value, ref):
    name = label(value, f"饰面图案 {ref} 的图片路径")
    relative = Path(name)
    if root is None or relative.anchor or ".." in relative.parts:
        raise InputError(f"饰面图案 {ref} 的图片须使用整区说明目录内的相对路径")
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise InputError(f"饰面图案 {ref} 的图片越出整区说明目录")
    try:
        with path.open("rb") as source:
            payload = source.read(ASSET_LIMIT + 1)
        if len(payload) > ASSET_LIMIT:
            raise InputError(f"饰面图案 {ref} 的图片超过 12 MiB")
        with Image.open(BytesIO(payload), formats=ASSET_FORMATS) as source:
            if max(source.size) > 8192 or source.width * source.height > 16_000_000:
                raise InputError(f"饰面图案 {ref} 的图片像素过多")
            if getattr(source, "n_frames", 1) != 1:
                raise InputError(f"饰面图案 {ref} 需要单张静态图片")
            return source.convert("RGBA"), source.format.lower(), payload
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise InputError(f"饰面图案 {ref} 的图片无法读取，请提供 PNG、JPEG 或 WebP") from error


def repeat_preview(swatch, extent, repeat, color, floor):
    scale = min(960 / extent[0], 590 / extent[1])
    size = tuple(max(1, round(length * scale)) for length in extent)
    background = Image.new("RGB", swatch.size, ImageColor.getrgb(color))
    background.paste(swatch, mask=swatch.getchannel("A"))
    indices = []
    for count, length, period, source_size in zip(size, extent, repeat, swatch.size, strict=True):
        distance = (np.arange(count) + .5) * length / count
        indices.append(np.minimum((distance % period / period * source_size).astype(int), source_size - 1))
    x, y = indices
    preview = Image.fromarray(np.asarray(background)[y[:, None], x[None, :]])
    if floor is not None:
        x0, _, _, y1 = floor.bounds
        mask = Image.new("L", size)
        vertices = [((px - x0) * size[0] / extent[0], (y1 - py) * size[1] / extent[1])
                    for px, py in floor.exterior.coords]
        ImageDraw.Draw(mask).polygon(vertices, fill=255)
        preview = Image.composite(preview, Image.new("RGB", size, "#f2f0eb"), mask)
    buffer = BytesIO()
    preview.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def surface_patterns(rows, items, asset_root, swatches=None):
    if not isinstance(rows, list) or len(rows) > 12:
        raise InputError("饰面图案须为不超过 12 项的列表")
    by_id = {item["id"]: item for item in items if item["category"] == "interior"}
    result = []
    used = set()
    for row in rows:
        object_fields(row, "ref name asset artwork repeat_mm note mode", "饰面图案")
        ref = label(row.get("ref"), "饰面图案的物件编号")
        item = by_id.get(ref)
        if item is None or item["kind"] not in WALL_KINDS | {"floor_finish"}:
            raise InputError(f"饰面图案 {ref} 须对应已校核的墙面或地面饰面")
        if ref in used:
            raise InputError(f"饰面图案的物件编号重复：{ref}")
        used.add(ref)
        name = label(row.get("name"), f"饰面图案 {ref} 的名称")
        note = label(row["note"], f"饰面图案 {ref} 的说明") if "note" in row else ""
        repeat = row.get("repeat_mm")
        if not isinstance(repeat, list) or len(repeat) != 2:
            raise InputError(f"饰面图案 {ref} 须提供两个毫米数值作为重复单元宽高")
        repeat = [number(value, f"饰面图案 {ref} 的重复尺寸", 1, 90000) for value in repeat]
        floor = None
        if item["kind"] == "floor_finish":
            floor = Polygon(item["polygon"])
            x0, y0, x1, y1 = floor.bounds
            extent = [x1 - x0, y1 - y0]
        else:
            dimensions = item["dimensions_mm"]
            extent = [dimensions["width_mm"], dimensions["height_mm"]]
        mode = row.get("mode", "repeat")
        if mode not in ("repeat", "single"):
            raise InputError(f"饰面图案 {ref} 的 mode 须为 repeat 或 single")
        if mode == "single" and any(abs(width - length) > .001 for width, length in zip(repeat, extent)):
            raise InputError(f"饰面图案 {ref} 的整幅尺寸须等于表面范围")
        pattern = {"ref": ref, "name": name, "note": note, "repeat_mm": repeat, "mode": mode,
                   "extent_mm": [round(value, 3) for value in extent],
                   "surface_kind": "floor" if floor is not None else "wall",
                   "status": "pending", "preview_data_uri": None}
        if "asset" in row and "artwork" in row:
            raise InputError(f"饰面图案 {ref} 请指定外部图片或原创纹样之一")
        if "asset" in row or "artwork" in row:
            if "artwork" in row:
                from artwork import render_artwork

                swatch = render_artwork(row["artwork"], repeat).convert("RGBA")
                buffer = BytesIO()
                swatch.save(buffer, format="PNG")
                extension, payload = "png", buffer.getvalue()
                pattern["artwork_source"] = "local_drawing"
            else:
                swatch, extension, payload = load_swatch(asset_root, row["asset"], ref)
            if mode == "single" and abs(swatch.width * repeat[1] / (swatch.height * repeat[0]) - 1) > .01:
                raise InputError(f"饰面图案 {ref} 的图片长宽比与整幅尺寸相差超过 1%")
            if swatches is not None:
                swatches[ref] = (extension, payload)
            pattern.update(status="ready", asset_sha256=sha256(payload).hexdigest(),
                           preview_data_uri=repeat_preview(swatch, extent, repeat, item["color"], floor))
        result.append(pattern)
    return result
