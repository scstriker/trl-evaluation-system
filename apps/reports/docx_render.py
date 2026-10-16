"""按固定模板生成 Word 报告。

1. 用 docxtpl 把前置信息填入 data/templates/report/report_template.docx（由
   scripts/prepare_report_template.py 从评价机构提供的原版式模板加工得到），封面、扉页、目录、页眉页脚沿用原版式；
2. 扉页印章：系统设置上传了印章图片时，作为浮动图片压在出具单位和日期上；否则显示“（盖章）”；
3. 在模板正文之后追加系统生成的章节（评价准则、等级评价、总结、附录）；
4. 设置“打开时更新域”，Word 打开时刷新目录页码。
"""

import copy
from io import BytesIO
from pathlib import Path

from django.conf import settings
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Mm, Pt
from docxtpl import DocxTemplate

TEMPLATE_PATH = Path(settings.BASE_DIR) / "data" / "templates" / "report" / "report_template.docx"
SEAL_MARK = "〔印章位置〕"
SEAL_WIDTH = Mm(40)
TEAM_MIN_ROWS = 5
BODY_PPR = (
    f'<w:pPr {nsdecls("w")}><w:spacing w:line="360" w:lineRule="auto"/>'
    '<w:ind w:firstLineChars="200" w:firstLine="480"/><w:jc w:val="both"/></w:pPr>'
)
BORDERS = (
    f'<w:tblBorders {nsdecls("w")}>'
    + "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="000000"/>' for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    + "</w:tblBorders>"
)


def template_context(front, report_no):
    """把前置信息整理成模板变量。评价组表每组至少 5 行，保持原版式的行数。"""
    blank = {"role": "", "name": "", "title": "", "specialty": "", "org": ""}

    def pad(rows):
        return list(rows) + [blank] * max(0, TEAM_MIN_ROWS - len(rows))

    context = {
        "report_no": report_no,
        "report_title": front["report_title"],
        "header_suffix": front["header_suffix"],
        "project_name": front["project_name"],
        "applicant": front["applicant"],
        "issuer": front["issuer"],
        "issue_date_cn": front["issue_date_cn"],
        "compiled_by": front["compiled_by"],
        "checked_by": front["checked_by"],
        "approved_by": front["approved_by"],
        "seal": SEAL_MARK,
        "basis": front["basis"],
        "team_caption": front["team_caption"],
        "self_rows": pad(front["self_rows"]),
        "third_rows": pad(front["third_rows"]),
        "modules": front["modules"],
    }
    context.update(front["sections"])
    return context


# ---------------------------------------------------------------------------
# 印章
# ---------------------------------------------------------------------------


def _seal_path():
    from apps.accounts.models import SystemConfig

    config = SystemConfig.get()
    if config.seal_image and Path(config.seal_image.path).exists():
        return config.seal_image.path
    return None


def _float_picture(run, offset_y):
    """把行内图片改为浮动图片：水平居中，相对所在段落下移 offset_y（EMU，可为负），不占行。"""
    inline = run._r.find(".//" + qn("wp:inline"))
    if inline is None:
        return
    extent = inline.find(qn("wp:extent"))
    doc_pr = inline.find(qn("wp:docPr"))
    graphic = inline.find(qn("a:graphic"))
    anchor = parse_xml(
        f'<wp:anchor {nsdecls("wp", "a", "pic", "r")} distT="0" distB="0" distL="0" distR="0" simplePos="0" '
        'relativeHeight="251659264" behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">'
        '<wp:simplePos x="0" y="0"/>'
        '<wp:positionH relativeFrom="column"><wp:align>center</wp:align></wp:positionH>'
        f'<wp:positionV relativeFrom="paragraph"><wp:posOffset>{int(offset_y)}</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{extent.get("cx")}" cy="{extent.get("cy")}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/>'
        f'<wp:docPr id="{doc_pr.get("id")}" name="seal"/><wp:cNvGraphicFramePr/>'
        "</wp:anchor>"
    )
    anchor.append(graphic)
    inline.getparent().replace(inline, anchor)


def _place_seal(document):
    for paragraph in document.paragraphs:
        if paragraph.text.strip() != SEAL_MARK:
            continue
        for run in paragraph.runs[1:]:
            run._r.getparent().remove(run._r)
        run = paragraph.runs[0]
        path = _seal_path()
        if path:
            run.text = ""
            run.add_picture(path, width=SEAL_WIDTH)
            # 印章中心落在下方“出具单位 / 日期”两行之间
            height = int(run._r.find(".//" + qn("wp:extent")).get("cy"))
            _float_picture(run, Mm(14) - height // 2)
        else:
            run.text = "（盖章）"
        return bool(path)
    return False


# ---------------------------------------------------------------------------
# 系统生成的章节
# ---------------------------------------------------------------------------


def _body(document, text, bold=False):
    paragraph = document.add_paragraph()
    paragraph._p.insert(0, parse_xml(BODY_PPR))
    run = paragraph.add_run(text)
    run.bold = bold
    return paragraph


def _caption(document, text):
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = Pt(6)
    run = paragraph.add_run(text)
    run.font.size = Pt(10.5)
    return paragraph


def _table(document, headers, rows, widths=None):
    table = document.add_table(rows=1, cols=len(headers))
    tbl_pr = table._tbl.tblPr
    tbl_pr.append(parse_xml(BORDERS))
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = str(header)
        cell._tc.get_or_add_tcPr().append(parse_xml(f'<w:shd {nsdecls("w")} w:val="clear" w:color="auto" w:fill="F2F2F2"/>'))
        for run in cell.paragraphs[0].runs:
            run.bold = True
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    for values in rows:
        cells = table.add_row().cells
        for index, value in enumerate(values):
            cells[index].text = "" if value is None else str(value)
    if widths:
        table.autofit = False
        for row in table.rows:
            for index, width in enumerate(widths):
                row.cells[index].width = Cm(width)
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(10.5)
    return table


def _heading_prototypes(document):
    """取模板里已有的一级、二级标题段落作为格式样板（模板标题带直接格式，只套样式会与原版式不一致）。"""
    h1 = h2 = None
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if h1 is None and paragraph.style is not None and paragraph.style.name == "Heading 1":
            h1 = paragraph
        elif h2 is None and text.startswith("2.1 "):
            h2 = paragraph
        if h1 is not None and h2 is not None:
            break
    return h1, h2


def _clone_heading(document, prototype, text, style):
    if prototype is None:
        return document.add_paragraph(text, style=style)
    paragraph = document.add_paragraph()
    p_pr = prototype._p.pPr
    if p_pr is not None:
        new_pr = copy.deepcopy(p_pr)
        for sect in new_pr.findall(qn("w:sectPr")):
            new_pr.remove(sect)
        paragraph._p.insert(0, new_pr)
    run = paragraph.add_run(text)
    if prototype.runs and prototype.runs[0]._r.rPr is not None:
        run._r.insert(0, copy.deepcopy(prototype.runs[0]._r.rPr))
    return paragraph


def append_blocks(document, blocks):
    h1, h2 = _heading_prototypes(document)
    for block in blocks:
        kind = block["kind"]
        if kind == "h1":
            _clone_heading(document, h1, block["text"], "Heading 1")
        elif kind == "h2":
            _clone_heading(document, h2, block["text"], "Heading 2")
        elif kind == "p":
            for line in block["text"].splitlines() or [""]:
                _body(document, line, bold=block.get("strong", False))
        elif kind == "caption":
            _caption(document, block["text"])
        elif kind == "table":
            _table(document, block["headers"], block["rows"], block.get("widths"))


def _update_fields_on_open(document):
    settings_element = document.settings.element
    if settings_element.find(qn("w:updateFields")) is None:
        settings_element.append(parse_xml(f'<w:updateFields {nsdecls("w")} w:val="true"/>'))


def clear_update_fields(docx_bytes):
    """Word 已刷新过目录的文件去掉“打开时更新域”，避免用户打开时再弹提示。"""
    document = Document(BytesIO(docx_bytes))
    for element in document.settings.element.findall(qn("w:updateFields")):
        element.getparent().remove(element)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def render(report, report_no):
    template = DocxTemplate(str(TEMPLATE_PATH))
    template.render(template_context(report["front"], report_no), autoescape=True)
    document = template.docx
    _place_seal(document)
    append_blocks(document, report["tail_blocks"])
    _update_fields_on_open(document)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
