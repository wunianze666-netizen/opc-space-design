"""Draw a designed material swatch from an editable, normalized drawing."""

from PIL import Image, ImageDraw

from model import InputError, color_hex, number, object_fields, points


def render_artwork(spec, extent):
    spec = object_fields(spec, "background layers", "原创纹样")
    color = color_hex(spec.get("background"), "纹样底色")
    layers = spec.get("layers")
    if not isinstance(layers, list) or not 1 <= len(layers) <= 200:
        raise InputError("原创纹样须有 1 至 200 个图层")
    scale = 1536 / max(extent)
    width, height = (max(1, round(value * scale)) for value in extent)
    image = Image.new("RGB", (width * 2, height * 2), color)
    draw = ImageDraw.Draw(image)
    for layer in layers:
        layer = object_fields(layer, "shape points color stroke", "原创纹样图层")
        kind = layer.get("shape")
        if kind not in ("rect", "ellipse", "line", "polygon"):
            raise InputError("原创纹样 shape 须为 rect、ellipse、line 或 polygon")
        minimum = 3 if kind == "polygon" else 2
        maximum = 120 if kind in ("polygon", "line") else 2
        xy = points(layer.get("points"), "原创纹样图层", minimum, maximum)
        if any(not 0 <= value <= 1 for point in xy for value in point):
            raise InputError("原创纹样的图层坐标须在 0 至 1 之间")
        xy = [(round(x * (image.width - 1)), round(y * (image.height - 1))) for x, y in xy]
        fill = color_hex(layer.get("color"), "原创纹样图层颜色")
        if kind == "line":
            stroke = number(layer.get("stroke", .01), "线宽（短边比例）", .0005, .25)
            draw.line(xy, fill=fill, width=max(1, round(stroke * min(image.size))), joint="curve")
        elif "stroke" in layer:
            raise InputError("仅 line 图层支持 stroke")
        elif kind == "polygon":
            draw.polygon(xy, fill=fill)
        else:
            if xy[1][0] < xy[0][0] or xy[1][1] < xy[0][1]:
                raise InputError("矩形和椭圆坐标依次为左上角、右下角")
            draw_shape = draw.rectangle if kind == "rect" else draw.ellipse
            draw_shape(xy, fill=fill)
    return image.resize((width, height), Image.Resampling.LANCZOS)
