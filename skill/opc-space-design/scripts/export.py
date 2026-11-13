"""DXF, PDF, Word and costing exports from the same layout data."""

import csv
from pathlib import Path
from xml.sax.saxutils import escape

import ezdxf
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from interior import design_layer
from layout import footprint, pieces, strategy_text
from model import as_ring, write_json
from visual import ACCENT, BG, INK, MUTED, axon_image, font_path, plan_image


STATUS = {"pass": "通过", "fail": "未通过", "warning": "提示", "pending": "待核"}
SCOPE = "计量范围为方案内的家具、屏风和地面饰材净面积。墙顶做法、灯具、五金、机电、消防、安装人工、税费及损耗另核。"


def money(cents):
    return "待报价" if cents is None else f"{cents / 100:,.2f}"


def dxf_export(project, placements, path):
    doc = ezdxf.new("R2013")
    doc.units = 4
    doc.header["$MEASUREMENT"] = 1
    msp = doc.modelspace()
    layers = {
        "OPC_OUTLINE": 7, "OPC_COLUMN": 8, "OPC_FIXED_WALL": 8, "OPC_SERVICE": 8,
        "OPC_FIXED": 8, "OPC_DOOR_SWING": 30, "OPC_KEEP_CLEAR": 30, "OPC_EXIT": 3,
        "OPC_WINDOW": 4, "OPC_USAGE_ENVELOPE": 9, "OPC_DIMENSIONS": 7,
    }
    for name, color in layers.items():
        doc.layers.new(name, dxfattribs={"color": color})

    def poly(pts, layer):
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})

    poly(as_ring(project.outline), "OPC_OUTLINE")
    if project.voids:
        doc.layers.new("OPC_VOID", dxfattribs={"color": 1})
        for _, shape in project.voids:
            poly(as_ring(shape), "OPC_VOID")
    if project.req["work_groups"]:
        doc.layers.new("OPC_WORK_GROUP", dxfattribs={"color": 4})
        doc.layers.new("OPC_WORK_GROUP_LABEL", dxfattribs={"color": 4})
        for name, group in project.req["work_groups"].items():
            shape = group["polygon"]
            poly(as_ring(shape), "OPC_WORK_GROUP")
            msp.add_text(name, dxfattribs={"height": 180, "insert": shape.representative_point().coords[0],
                                         "layer": "OPC_WORK_GROUP_LABEL"})
    fixed_layers = {"column": "OPC_COLUMN", "wall": "OPC_FIXED_WALL",
                    "service": "OPC_SERVICE", "existing": "OPC_FIXED",
                    "door_swing": "OPC_DOOR_SWING", "reserved": "OPC_KEEP_CLEAR", "no_walk": "OPC_NO_WALK"}
    for _, kind, shape in project.fixed + project.clear:
        layer = fixed_layers[kind]
        if layer not in doc.layers:
            doc.layers.new(layer, dxfattribs={"color": 30})
        poly(as_ring(shape), layer)
    for group, layer in [(project.exits, "OPC_EXIT"), (project.windows, "OPC_WINDOW")]:
        for edge in group:
            a, b = edge.line.coords
            msp.add_line(a, b, dxfattribs={"layer": layer})
    if project.req["clear_routes"]:
        doc.layers.new("OPC_PROTECTED_ROUTE", dxfattribs={"color": 4})
        doc.layers.new("OPC_ROUTE_CENTER", dxfattribs={"color": 140})
        for line, _, shape in project.req["clear_routes"].values():
            poly(as_ring(shape), "OPC_PROTECTED_ROUTE")
            msp.add_lwpolyline(line.coords, dxfattribs={"layer": "OPC_ROUTE_CENTER"})
    if project.req["required_approaches"]:
        layer = "OPC_REQUIRED_APPROACH"
        doc.layers.new(layer, dxfattribs={"color": 6})
        for name, (point, _) in project.req["required_approaches"].items():
            msp.add_point(point.coords[0], dxfattribs={"layer": layer})
            msp.add_text(name, dxfattribs={"height": 140, "insert": point.coords[0], "layer": layer})
    if project.req["interior"]:
        doc.layers.new("OPC_INTERIOR_LABEL", dxfattribs={"color": 5})
        for element in project.req["interior"]["elements"]:
            layer = design_layer(element["kind"])
            if layer not in doc.layers:
                doc.layers.new(layer, dxfattribs={"color": 5})
            shape = element["footprint"]
            poly(as_ring(shape), layer)
            msp.add_text(element["name"], dxfattribs={"height": 150, "insert": shape.representative_point().coords[0],
                                                     "layer": "OPC_INTERIOR_LABEL"})
    for placement in placements:
        poly(as_ring(footprint(placement, project)), "OPC_USAGE_ENVELOPE")
    for item in pieces(placements, project):
        layer = "FURN_" + item["kind"].upper()
        if layer not in doc.layers:
            doc.layers.new(layer, dxfattribs={"color": 2 if "chair" in layer.lower() else 42})
        poly(item["polygon"], layer)
    x0, y0, x1, y1 = project.outline.bounds
    for p1, p2, base, angle in [
        ((x0, y0), (x1, y0), (x0, y0 - 520), 0),
        ((x0, y0), (x0, y1), (x0 - 520, y0), 90),
    ]:
        dim = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle,
                                 override={"dimtxt": 160, "dimasz": 110, "dimdec": 0},
                                 dxfattribs={"layer": "OPC_DIMENSIONS"})
        dim.render()
    msp.add_text("DIMENSIONS IN MM | CONCEPT LAYOUT",
                 dxfattribs={"height": 170, "insert": (x0, y1 + 520), "layer": "OPC_DIMENSIONS"})
    doc.set_modelspace_vport((y1 - y0) * 1.3, center=((x0 + x1) / 2, (y0 + y1) / 2))
    doc.saveas(path)


def pdf_font():
    name = "OPCChinese"
    if name in pdfmetrics.getRegisteredFontNames():
        return name
    try:
        pdfmetrics.registerFont(TTFont(name, font_path(), subfontIndex=0))
    except Exception:
        name = "STSong-Light"
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(name))
    return name


def image_pdf(image, path):
    w, h = landscape(A4)
    c = canvas.Canvas(str(path), pagesize=(w, h))
    c.drawImage(ImageReader(image), 0, 0, width=w, height=h, preserveAspectRatio=True, anchor="c")
    c.showPage()
    c.save()


def option_files(root, project, option, export_views=True, *, plan_pdf=True):
    folder = root / option["id"]
    folder.mkdir()
    placements = option["placements"]
    plan = plan_image(project, placements, f"方案 {option['id']}", option["strategy"])
    plan.save(folder / "平面图.png")
    if plan_pdf:
        image_pdf(plan, folder / "平面图.pdf")
    dxf_export(project, placements, folder / "平面图.dxf")
    if export_views:
        for i, angle in enumerate((45, 225), 1):
            axon_image(project, placements, f"{option['id']} / 视角 {i}", angle).save(folder / f"轴测-{i}.png")
    with (folder / "材料清单.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["项目", "数量", "单位", "参考单价 元", "参考小计 元"])
        for row in option["audit"]["items"]:
            name = row["name"]
            if name.lstrip().startswith(("=", "+", "-", "@")):
                name = "'" + name
            writer.writerow([name, row["quantity"], row["unit"],
                             money(row["unit_cents"]).replace(",", ""), money(row["amount_cents"]).replace(",", "")])
    if project.req["interior"]:
        labels = {"slats": "格栅", "acoustic": "吸声饰面", "art": "墙面艺术", "display_wall": "展示墙", "notice_board": "可更换壁报",
                  "floor_finish": "地面饰面", "ceiling_light": "灯具", "ceiling_baffle": "吊挂吸声片"}
        with (folder / "室内设计清单.csv").open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["元素", "做法", "材质代号", "底标高 mm", "顶标高 mm", "平面投影 m²", "深化"])
            for item in project.req["interior"]["elements"]:
                name = item["name"]
                if name.lstrip().startswith(("=", "+", "-", "@")):
                    name = "'" + name
                writer.writerow([name, labels[item["kind"]], item["material"], item["bottom_mm"], item["top_mm"],
                                 f"{item['footprint'].area / 1e6:.4f}", "选样、安装和报价待核"])
    write_json(folder / "layout.json", option)


def program_text(project):
    counts = []
    if project.req["desks"]:
        counts.append(f"{project.req['desks']} 工位")
    if project.req["furniture"]:
        counts.append(f"{sum(row['count'] for row in project.req['furniture'].values())} 件或组指定家具")
    return " · ".join(counts) or "按指定配套布置"


def summary_text(project, option):
    audit = option["audit"]
    rule = f"{program_text(project)}，项目通行净宽 {project.req['aisle_mm']} mm。"
    cost = budget_text(audit) + "。"
    if audit["missing_prices"]:
        cost += " 待报价：" + "、".join(audit["missing_prices"]) + "。"
    return rule + cost


def budget_text(audit):
    if any(row["unit_cents"] is not None for row in audit["items"]):
        return f"已报价合计 ¥{money(audit['subtotal_cents'])}"
    return "预算待报价"


def reports(root, project, options, stats, revision, recommendation):
    build_pdf(root, project, options, stats, revision, recommendation)
    build_word(root, project, options, stats, revision, recommendation)


def build_pdf(root, project, options, stats, revision, recommendation):
    w, h = landscape(A4)
    c = canvas.Canvas(str(root / "方案说明.pdf"), pagesize=(w, h))
    c.setTitle(project.title + " · 空间设计方案")
    face = pdf_font()
    styles = {}

    def para(text, x, top, width, size=10, color=INK):
        key = (size, color)
        if key not in styles:
            styles[key] = ParagraphStyle("opc", fontName=face, fontSize=size,
                                         leading=size * 1.47, alignment=TA_LEFT, wordWrap="CJK",
                                         textColor=colors.HexColor(color))
        p = Paragraph(escape(text), styles[key])
        _, height = p.wrap(width, h)
        p.drawOn(c, x, top - height)
        return top - height

    def base(label):
        c.setFillColor(colors.HexColor(BG))
        c.rect(0, 0, w, h, fill=1, stroke=0)
        para(label, 37, h - 30, w - 74, 12, ACCENT)
        c.setStrokeColor(colors.HexColor("#c3c9ba"))
        c.line(37, 39, w - 37, 39)
        para(f"空间方案研究  /  {revision}  /  几何和参考预算以本包数据为准", 37, 30, w - 74, 9, MUTED)

    base("OPC  /  空间设计提案")
    y = para(project.title, 37, h - 68, w - 74, 27) - 18
    y = para(f"{project.outline.area / 1e6:.1f} m²   ·   {program_text(project)}   ·   {len(options)} 套方案",
             37, y, w - 74, 16, ACCENT) - 17
    if options:
        hero = root / options[0]["id"] / "轴测-1.png"
        if not hero.exists():
            hero = root / options[0]["id"] / "平面图.png"
        c.drawImage(str(hero), 32, 55, 515, 355, preserveAspectRatio=True, anchor="c")
        yy = y - 10
        for option in options:
            name, _ = strategy_text(project, option["strategy"])
            yy = para(f"{option['id']}  {name}", 570, yy, 230, 15) - 5
            yy = para(summary_text(project, option), 570, yy, 227, 9, MUTED) - 16
        recommended = recommendation["recommended_option"]
        if recommended:
            tied = recommendation["tied_first_options"]
            title = f"方案 {'、'.join(tied)} 并列" if len(tied) > 1 else f"本次建议查看方案 {recommended}"
            yy = para(title, 570, yy, 225, 11, ACCENT) - 4
            para("。".join(recommendation["ranking"][0]["reasons"][:2]), 570, yy, 225, 9, MUTED)
        elif recommendation["skipped_priorities"]:
            para("优先项暂不可比较，详见清单及推荐记录。", 570, yy, 225, 9, MUTED)
    else:
        para(stats.get("reason", "当前搜索未找到满足已配置条件的方案。可核对几何、工位数、配套及预算后再试。"),
             37, y, w - 74, 14)
    c.showPage()
    for option in options:
        name, rationale = strategy_text(project, option["strategy"])
        base(f"方案 {option['id']}  /  {name}")
        para(project.title, 37, h - 63, w - 74, 19)
        para(rationale + " " + summary_text(project, option), 37, h - 101, w - 74, 11)
        folder = root / option["id"]
        c.drawImage(str(folder / "平面图.png"), 31, 139, 446, 313, preserveAspectRatio=True, anchor="c")
        for i, y in ((1, 278), (2, 111)):
            view = folder / f"轴测-{i}.png"
            if view.exists():
                c.drawImage(str(view), 484, y, 321, 160, preserveAspectRatio=True, anchor="c")
        para("配色与材质  " + project.req["style"] + "，按布局体块表现，详图可继续深化。",
             37, 126, 427, 10, MUTED)
        para("平面图标注为包络总尺寸。DXF 中保留实际坐标、固定结构、开口和家具使用空间。",
             37, 102, 427, 9, MUTED)
        c.showPage()

        base(f"方案 {option['id']}  /  材料与检查")
        para("材料清单与预算参考", 37, h - 62, w - 74, 19)
        audit = option["audit"]
        rows = [["项目", "数量", "单位", "参考单价 元", "参考小计 元"]]
        item_style = ParagraphStyle("opc-item", fontName=face, fontSize=9, leading=12, wordWrap="CJK")
        rows += [[Paragraph(escape(r["name"]), item_style), r["quantity"], r["unit"], money(r["unit_cents"]), money(r["amount_cents"])]
                 for r in audit["items"]]
        table = Table(rows, colWidths=[135, 40, 36, 62, 68], repeatRows=1)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), face), ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0e7db")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#faf9f3"), colors.HexColor("#eeefe7")]),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(INK)),
        ]))
        y = para("规则检查", 411, h - 104, 394, 15, ACCENT) - 7
        for row in audit["checks"]:
            detail = row["description"]
            if row.get("upper_bound_mm") is not None:
                detail += f"（上界 {row['upper_bound_mm']} mm）"
            y = para(f"{STATUS[row['status']]}  {detail}", 411, y, 389, 9, INK) - 6
        para("检查结论对应输入与项目配置。现场尺寸、施工和法定审查在项目深化阶段确认。",
             411, max(y - 3, 57), 389, 9, MUTED)
        source = project.raw.get("prices", {})
        available = h - 104 - 147
        while True:
            parts = table.split(345, available)
            page_table = parts[0]
            _, th = page_table.wrap(345, available)
            page_table.drawOn(c, 37, h - 104 - th)
            if len(parts) == 1:
                y = para(budget_text(audit), 37, h - 115 - th, 348, 12, ACCENT) - 7
                para(SCOPE + f" 价格来源：{source.get('source', '待提供')}，日期：{source.get('date', '待提供')}。",
                     37, y, 349, 9, MUTED)
                break
            c.showPage()
            base(f"方案 {option['id']}  /  材料清单续页")
            para("材料清单与预算参考", 37, h - 62, w - 74, 19)
            table = parts[1]
        c.showPage()
    c.save()


def build_word(root, project, options, stats, revision, recommendation):
    d = Document()
    s = d.sections[0]
    s.page_width, s.page_height = Cm(21), Cm(29.7)
    s.left_margin = s.right_margin = Cm(1.8)
    s.top_margin, s.bottom_margin = Cm(1.5), Cm(1.5)
    for name in ("Normal", "Title", "Heading 1", "Heading 2"):
        style = d.styles[name]
        style.font.name = "Microsoft YaHei"
        style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(10.5 if name == "Normal" else 13 if name != "Title" else 19)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.paragraph_format.space_after = Pt(5)
        style.paragraph_format.line_spacing = 1.13
        properties = style._element.find(qn("w:pPr"))
        if properties is not None:
            border = properties.find(qn("w:pBdr"))
            if border is not None:
                properties.remove(border)
    d.core_properties.title = project.title + " · 空间设计方案"
    d.core_properties.author = ""
    d.add_paragraph(project.title, "Title")
    d.add_paragraph(f"空间设计方案  /  {revision}  /  {project.outline.area / 1e6:.1f} m²")
    d.add_paragraph("图纸来源：" + project.raw["geometry"]["source"])
    if not options:
        d.add_paragraph(stats.get("reason", "当前搜索未找到通过已配置约束的候选方案。"))
    for option in options:
        name, rationale = strategy_text(project, option["strategy"])
        heading = d.add_heading(f"方案 {option['id']}  {name}", 1)
        heading.paragraph_format.page_break_before = option is not options[0]
        d.add_paragraph(rationale)
        d.add_paragraph(summary_text(project, option))
        d.add_picture(str(root / option["id"] / "平面图.png"), width=Cm(14.8))
        d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        img = root / option["id"] / "轴测-1.png"
        if img.exists():
            d.add_picture(str(img), width=Cm(11.8))
            d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        d.add_paragraph("平面尺寸以 DXF 为准，轴测图按同一家具和开口位置绘制。")
        heading = d.add_heading(f"方案 {option['id']}  清单与规则检查", 2)
        heading.paragraph_format.page_break_before = True
        table = d.add_table(rows=1, cols=5)
        table.style = "Table Grid"
        for c, value in zip(table.rows[0].cells, ["材料或家具", "数量", "单位", "参考单价", "参考小计"]):
            c.text = value
        for row in option["audit"]["items"]:
            cells = table.add_row().cells
            for c, value in zip(cells, [row["name"], row["quantity"], row["unit"], money(row["unit_cents"]), money(row["amount_cents"])]):
                c.text = value
        for index, row in enumerate(table.rows):
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
            if index == 0:
                row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
            for cell in row.cells:
                if index % 2 == 0:
                    shade = OxmlElement("w:shd")
                    shade.set(qn("w:fill"), "DEE7DD" if index == 0 else "F1F5EF")
                    cell._tc.get_or_add_tcPr().append(shade)
                for p in cell.paragraphs:
                    p.paragraph_format.space_after = Pt(2)
                    for run in p.runs:
                        run.font.size = Pt(9.5)
                        run.bold = index == 0
        source = project.raw.get("prices", {})
        if option is options[0] and recommendation["recommended_option"]:
            tied = recommendation["tied_first_options"]
            title = f"方案 {'、'.join(tied)} 并列。" if len(tied) > 1 else f"建议查看方案 {recommendation['recommended_option']}。"
            d.add_paragraph(title + recommendation["ranking"][0]["reasons"][0])
        d.add_paragraph(SCOPE)
        d.add_paragraph(f"参考单价来源：{source.get('source', '待提供')}。日期：{source.get('date', '待提供')}。")
        for rule in option["audit"]["checks"]:
            d.add_paragraph(f"{STATUS[rule['status']]}  {rule['description']}")
    d.save(root / "方案说明.docx")
