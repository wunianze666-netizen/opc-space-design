"""Make an exact-text feature-wall graphic for a single measured surface."""

import argparse
from io import BytesIO
from pathlib import Path
from re import finditer
from unicodedata import category, name

from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

from model import InputError, number
from visual import font


GRAPHITE = "#202728"
IVORY = "#F2EBDB"
COPPER = "#D5A27C"
SAGE = "#B6C29A"
GUIDE = "#65736F"
GRID = "#333D3D"


def raster_size(width_mm, height_mm):
    ratio = width_mm / height_mm
    width_is_long = ratio >= 1
    aspect = ratio if width_is_long else 1 / ratio
    for long_side in range(3072, 8193):
        short_side = max(1, round(long_side / aspect))
        size = (long_side, short_side) if width_is_long else (short_side, long_side)
        if size[0] * size[1] <= 16_000_000 and abs(size[0] / size[1] / ratio - 1) <= .01:
            return size
    raise InputError("墙面长宽比无法在饰面图片尺寸上限内准确表达")


def check_text(value, description, *, bold):
    if (not isinstance(value, str) or not value.strip() or len(value) > 200
            or any(category(ch) in {"Cc", "Cs"} for ch in value)):
        raise InputError(f"{description}应为 1–200 字符的单行文字")
    face = font(32, bold)
    missing = face.getmask("\U0010ffff")
    missing_pattern = (missing.size, bytes(missing))
    for char in dict.fromkeys(value):
        if char.isspace() or category(char) in {"Cf", "Mn"}:
            continue
        glyph = face.getmask(char)
        if (glyph.size, bytes(glyph)) == missing_pattern:
            option = "OPC_FONT_BOLD 或 OPC_FONT" if bold else "OPC_FONT"
            raise InputError(f"当前字体缺少字形 {char}。请设置 {option} 指向覆盖品牌字形的字体")


def title_options(text, bold):
    options = [text]
    splits = [(match.start(), match.end()) for match in finditer(r" +", text) if 0 < match.start() < match.end() < len(text)]
    if not splits and len(text) > 8:
        splits = [(i, i) for i in range(2, len(text) - 1)
                  if all(name(ch, "").startswith("CJK") for ch in text[i - 1:i + 1])]
    if splits:
        face = font(72, bold)
        start, end = min(splits, key=lambda pair: max(face.getlength(text[:pair[0]]), face.getlength(text[pair[1]:])))
        options.append(text[:start] + "\n" + text[end:])
    return options


def fit_text(draw, value, width, height, *, bold, wrap):
    options = title_options(value, bold) if wrap else [value]
    best = None
    for text in options:
        low, high = 1, max(1, round(height * 3))
        while low <= high:
            size = (low + high) // 2
            face = font(size, bold)
            spacing = max(1, round(size * .14))
            bounds = draw.multiline_textbbox((0, 0), text, font=face, spacing=spacing)
            if bounds[2] - bounds[0] <= width and bounds[3] - bounds[1] <= height:
                score = size * (.9 if "\n" in text else 1)
                if best is None or score > best[0]:
                    best = (score, text, face, spacing, bounds)
                low = size + 1
            else:
                high = size - 1
    if best is None or best[2].size < 4:
        raise InputError("墙面比例与品牌字数无法形成清晰的图稿")
    return best[1:]


def draw_fitted(draw, fitted, x, y):
    text, face, spacing, (left, top, _, bottom) = fitted
    draw.multiline_text((x - left, y - top), text, font=face, spacing=spacing, fill=IVORY)
    return bottom - top


def draw_schematic(draw, width, height, margin, divider):
    scale = min(width, height)
    x0, y0 = margin, divider + height * .065
    dx, dy = width - 2 * margin, height * .915 - y0

    def xy(x, y):
        return x0 + x * dx, y0 + y * dy

    def line(points, color, thickness=.0015):
        draw.line([xy(x, y) for x, y in points], fill=color,
                  width=max(1, round(scale * thickness)), joint="curve")

    def marker(x, y, color, radius):
        cx, cy = xy(x, y)
        r = max(2, scale * radius)
        r_outer = r * 1.6
        draw.ellipse((cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer), fill=GRAPHITE,
                     outline=color, width=max(1, round(scale * .001)))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)

    columns = min(20, max(6, round(dx / max(1, dy) * 6)))
    for i in range(columns + 1):
        line([(i / columns, .03), (i / columns, .98)], GRID, .0008)
    for i in range(7):
        line([(0, .05 + i * .145), (1, .05 + i * .145)], GRID, .0008)

    outline = [(.24, .13), (.68, .13), (.68, .02), (.97, .02), (.97, .72),
               (.87, .72), (.87, .97), (.24, .97), (.24, .13)]
    line(outline, GUIDE, .0018)
    line([(.24, .29), (.38, .29), (.38, .13)], GUIDE)
    line([(.24, .80), (.37, .80), (.37, .97)], GUIDE)
    line([(.70, .54), (.88, .54), (.88, .02)], GUIDE)
    line([(.62, .97), (.62, .76), (.87, .76)], GUIDE)
    void_start, void_end = xy(.53, .31), xy(.68, .59)
    draw.rounded_rectangle((*void_start, *void_end), radius=max(1, round(scale * .006)),
                           fill="#262E2E", outline=GUIDE, width=max(1, round(scale * .0015)))
    line([(.548, .344), (.662, .555)], "#465250", .001)
    line([(.548, .555), (.662, .344)], "#465250", .001)

    route = [(.025, .75), (.14, .75), (.14, .47), (.31, .47), (.31, .69), (.47, .69),
             (.47, .24), (.62, .24), (.62, .10), (.80, .10), (.80, .36), (.91, .36), (.91, .79)]
    line(route, COPPER, .0056)
    line([(.31, .69), (.31, .91), (.43, .91)], SAGE, .0024)
    line([(.80, .10), (.94, .10), (.94, .20)], SAGE, .0024)
    for x, y, color in ((.025, .75, COPPER), (.31, .69, SAGE), (.47, .24, COPPER),
                        (.80, .10, SAGE), (.91, .79, COPPER)):
        marker(x, y, color, .0085)
    marker(.43, .91, SAGE, .0043)
    marker(.94, .20, SAGE, .0043)

    line([(0, 1.02), (1, 1.02)], GUIDE, .001)
    for i in range(columns + 1):
        line([(i / columns, 1.02), (i / columns, 1.04)], GUIDE, .001)
    face = font(max(7, round(scale * .017)))
    draw.text(xy(0, 1.075), "0", fill="#98A29D", font=face, anchor="lt")
    draw.text(xy(1, 1.075), "X", fill="#98A29D", font=face, anchor="rt")
    draw.text(xy(0, -.05), "Y", fill="#98A29D", font=face, anchor="lt")


def render_brand_wall(brand, width_mm, height_mm, subtitle=None):
    width_mm = number(width_mm, "墙面宽度 mm", .01, 90_000)
    height_mm = number(height_mm, "墙面高度 mm", .01, 90_000)
    check_text(brand, "品牌名称", bold=True)
    if subtitle is not None:
        check_text(subtitle, "副标题", bold=False)
    width, height = raster_size(width_mm, height_mm)
    image = Image.new("RGB", (width, height), GRAPHITE)
    draw = ImageDraw.Draw(image)
    margin = round(width * .078)
    headline = fit_text(draw, brand, width - 2 * margin, height * (.23 if subtitle else .30), bold=True, wrap=True)
    title_y = height * .095
    text_bottom = title_y + draw_fitted(draw, headline, margin, title_y)
    if subtitle:
        sub = fit_text(draw, subtitle, width - 2 * margin, height * .068, bold=False, wrap=False)
        sub_y = text_bottom + height * .027
        text_bottom = sub_y + draw_fitted(draw, sub, margin, sub_y)
    divider = height * .455
    if width < height * .8:
        divider = min(divider, max(height * .345, text_bottom + height * .045))
    draw.line([(margin, divider), (width - margin, divider)], fill=GUIDE,
              width=max(1, round(min(width, height) * .0011)))
    draw_schematic(draw, width, height, margin, divider)
    return image


def save_brand_wall(out, brand, width_mm, height_mm, subtitle=None):
    out = Path(out).expanduser()
    if out.suffix.lower() != ".png":
        raise InputError("品牌墙图稿的输出文件名须以 .png 结尾")
    if out.exists() or out.is_symlink():
        raise InputError(f"输出已存在，请指定新路径：{out}")
    image = render_brand_wall(brand, width_mm, height_mm, subtitle)
    metadata = PngInfo()
    metadata.add_itxt("Brand", brand)
    if subtitle is not None:
        metadata.add_itxt("Subtitle", subtitle)
    metadata.add_text("PhysicalSizeMM", f"{width_mm:g} x {height_mm:g}")
    metadata.add_text("Application", "One full feature-wall face. Do not repeat as a tile.")
    png = BytesIO()
    dpi = (image.width / width_mm * 25.4, image.height / height_mm * 25.4)
    image.save(png, format="PNG", pnginfo=metadata, dpi=dpi, optimize=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        stream = out.open("xb")
    except FileExistsError as exc:
        raise InputError(f"输出已存在，请指定新路径：{out}") from exc
    try:
        with stream:
            stream.write(png.getbuffer())
    except OSError:
        out.unlink(missing_ok=True)
        raise
    return image.size


def main():
    parser = argparse.ArgumentParser(description="生成单幅可按墙面尺寸贴用的品牌 PNG")
    parser.add_argument("--brand", required=True, help="按原字形排版的品牌名称")
    parser.add_argument("--subtitle", help="可选副标题")
    parser.add_argument("--width-mm", type=float, required=True, help="实际墙面宽度")
    parser.add_argument("--height-mm", type=float, required=True, help="实际墙面高度")
    parser.add_argument("--out", type=Path, required=True, help="新 PNG 路径")
    args = parser.parse_args()
    try:
        width, height = save_brand_wall(args.out, args.brand, args.width_mm, args.height_mm, args.subtitle)
    except (InputError, OSError) as exc:
        parser.exit(2, f"{exc}\n")
    print(f"{args.out.resolve()}  {width} × {height} px")


if __name__ == "__main__":
    main()
