"""在企业「项目概况」原版式模板上填入正文（保留 Heading 1 / 样式「1」等，与正式报告一致）。"""

import copy
import logging
import shutil
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from django.conf import settings
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.table import Table
from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)

BASE = Path(settings.BASE_DIR)
SOURCE = BASE / "data" / "templates" / "report" / "source" / "技术就绪度评价报告_原版式空白模板.docx"
ENTERPRISE_OVERVIEW = BASE / "data" / "templates" / "enterprise" / "项目概况-模板.docx"
ENTERPRISE_PACKAGE = BASE / "data" / "templates" / "enterprise" / "企业报告材料-模板.docx"
WBST_TEMPLATE = BASE / "data" / "templates" / "enterprise" / "附录A-WBST-模板.docx"
SUPPORT_TEMPLATE = BASE / "data" / "templates" / "enterprise" / "评价支撑信息-模板.docx"
GOLD_WBST = Path(
    r"C:\Users\61973\Desktop\Project\261115-TRLDemo-第四次修改"
    r"\发陈实-技术就绪度软件相关\发陈实-技术就绪度软件相关"
    r"\表c.1 开发研究项目WBST模板 - V2.docx"
)

PACKAGE_INSTRUCTION = "请填写第1～3章与附录A/B；勿增写第4章及以后（由系统生成）。"
APPENDIX_A_HEADING = "附录A  WBST表格"
APPENDIX_B_HEADING = "附录B  评价支撑信息清单"

BODY_PPR = (
    f'<w:pPr {nsdecls("w")}><w:spacing w:line="360" w:lineRule="auto"/>'
    '<w:ind w:firstLineChars="200" w:firstLine="480"/><w:jc w:val="both"/></w:pPr>'
)


def _text_of(paragraph):
    return "".join(run.text for run in paragraph.runs).strip()


def _paragraphs(doc):
    return [Paragraph(el, doc._body) for el in doc.element.body.iterchildren(qn("w:p"))]


def _find(doc, text):
    for paragraph in _paragraphs(doc):
        if _text_of(paragraph) == text:
            return paragraph
    raise ValueError(f"模板中找不到段落：{text}")


def _set_text(paragraph, text):
    if not paragraph.runs:
        paragraph.add_run(text)
        return
    paragraph.runs[0].text = text
    for run in paragraph.runs[1:]:
        run._r.getparent().remove(run._r)


def _is_placeholder_paragraph(paragraph):
    raw = _text_of(paragraph)
    normalized = raw.replace("\u3000", "").replace(" ", "").replace("\t", "")
    return not normalized


def _remove_next_placeholder(heading):
    nxt = heading._p.getnext()
    if nxt is None or nxt.tag != qn("w:p"):
        return
    paragraph = Paragraph(nxt, heading._parent)
    if _is_placeholder_paragraph(paragraph):
        nxt.getparent().remove(nxt)


def _new_body_paragraph(prototype, parent, line):
    if prototype is not None and prototype._p.pPr is not None:
        p = copy.deepcopy(prototype._p)
        for child in list(p):
            if child.tag != qn("w:pPr"):
                p.remove(child)
        if prototype.runs and prototype.runs[0]._r.rPr is not None:
            r = copy.deepcopy(prototype.runs[0]._r)
            for child in list(r):
                if child.tag != qn("w:rPr"):
                    r.remove(child)
            p.append(r)
        paragraph = Paragraph(p, parent)
        if paragraph.runs:
            paragraph.runs[0].text = line
        else:
            paragraph.add_run(line)
        return p
    p = parse_xml(f'<w:p {nsdecls("w")}/>')
    p.append(parse_xml(BODY_PPR))
    Paragraph(p, parent).add_run(line)
    return p


def _body_after(heading, paragraphs, prototype=None):
    _remove_next_placeholder(heading)
    anchor = heading._p
    last = heading
    for block in paragraphs:
        for line in block.split("\n"):
            line = line.strip()
            if not line:
                continue
            p = _new_body_paragraph(prototype, heading._parent, line)
            anchor.addnext(p)
            anchor = p
            last = Paragraph(p, heading._parent)
    return last


TEAM_FIELDS = ("role", "name", "title", "specialty", "org")


def _team_row_values(row):
    if isinstance(row, dict):
        return [str(row.get(field, "") or "") for field in TEAM_FIELDS]
    return [str(v) for v in row]


def _fill_team_table(table, self_rows, third_rows):
    third_start = next(
        (i for i, row in enumerate(table.rows) if row.cells[0].text.strip() == "第三方评价组"),
        None,
    )
    if third_start is None:
        return

    def write_block(start, end, rows):
        for offset in range(end - start):
            if offset >= len(rows):
                for cell in table.rows[start + offset].cells:
                    cell.text = ""
                continue
            values = _team_row_values(rows[offset])
            cells = table.rows[start + offset].cells
            for col, value in enumerate(values):
                cells[col].text = value

    write_block(2, third_start, self_rows or [])
    write_block(third_start + 2, len(table.rows), third_rows or [])


def restore_team_section_from_front(docx_bytes: bytes, front: dict) -> bytes:
    """企业概况 Word 合并后，用系统登记的评价组成员重新填入表 2-1。"""
    doc = Document(BytesIO(docx_bytes))
    caption = None
    for paragraph in _paragraphs(doc):
        normalized = _text_of(paragraph).replace("\u3000", "").replace(" ", "")
        if "表2-1" in normalized and "评价组" in normalized:
            caption = paragraph
            break
    if caption is None:
        return docx_bytes
    el = caption._p.getnext()
    while el is not None and el.tag != qn("w:tbl"):
        el = el.getnext()
    if el is None:
        return docx_bytes
    table = Table(el, caption._parent)
    _fill_team_table(table, front.get("self_rows") or [], front.get("third_rows") or [])
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _normalized_heading(text: str) -> str:
    return text.replace("\u3000", " ").replace("\t", " ").strip()


def _is_system_chapter_start(text: str) -> bool:
    normalized = _normalized_heading(text)
    if normalized.startswith("4 ") and "评价准则" in normalized:
        return True
    for marker in ("5 技术就绪度评价", "5 制造成熟度评价", "6 总结", "7 附录"):
        if normalized == marker or normalized.startswith(marker):
            return True
    if normalized.startswith("附录A") or normalized.startswith("附录B"):
        return True
    return False


def _trim_source_document(doc: Document) -> None:
    """保留第 1～3 章正文，去掉封面/目录及第 4 章及以后（若有）。"""
    body = doc.element.body
    children = list(body)
    start = None
    for idx, child in enumerate(children):
        if child.tag != qn("w:p"):
            continue
        if _normalized_heading(_text_of(Paragraph(child, doc))) == "1 概况":
            start = idx
            break
    end = None
    for idx, child in enumerate(children):
        if child.tag != qn("w:p"):
            continue
        if _is_system_chapter_start(_text_of(Paragraph(child, doc))):
            end = idx
            break
    sect = next((c for c in reversed(children) if c.tag == qn("w:sectPr")), None)
    if start is not None:
        for child in children[:start]:
            if child is not sect:
                body.remove(child)
    if end is not None:
        for child in children[end:]:
            if child is not sect:
                body.remove(child)


def _write_trimmed_overview_docx(dest: Path) -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SOURCE, dest)
    doc = Document(dest)
    _trim_source_document(doc)
    doc.save(dest)


def _prepend_bold_instruction(doc: Document, text: str) -> None:
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.bold = True
    blank = doc.add_paragraph()
    body = doc.element.body
    for element in (para._p, blank._p):
        body.remove(element)
    body.insert(0, blank._p)
    body.insert(0, para._p)


def _wbst_source_path() -> Path:
    if WBST_TEMPLATE.is_file():
        return WBST_TEMPLATE
    if GOLD_WBST.is_file():
        return GOLD_WBST
    raise FileNotFoundError(f"未找到 WBST 模板：{WBST_TEMPLATE} 或 {GOLD_WBST}")


def _support_table_only_bytes() -> bytes:
    if SUPPORT_TEMPLATE.is_file():
        source = Document(SUPPORT_TEMPLATE)
    else:
        source = Document()
        source.add_table(rows=2, cols=4)
        headers = ["编号", "相关文件（佐证材料）", "关联细则", "备注"]
        for i, label in enumerate(headers):
            source.tables[0].rows[0].cells[i].text = label
        source.tables[0].rows[1].cells[0].text = "（示例）"
    out = Document()
    body = out.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    for child in source.element.body:
        if child.tag == qn("w:tbl"):
            body.append(copy.deepcopy(child))
    sect = next((c for c in reversed(list(source.element.body)) if c.tag == qn("w:sectPr")), None)
    if sect is not None:
        body.append(copy.deepcopy(sect))
    buffer = BytesIO()
    out.save(buffer)
    return buffer.getvalue()


def _append_appendices_python(base_bytes: bytes, wbst_bytes: bytes, support_bytes: bytes) -> bytes:
    from apps.reports.docx_merge import append_docx_bytes

    doc = Document(BytesIO(base_bytes))
    doc.add_paragraph(APPENDIX_A_HEADING)
    append_docx_bytes(doc, wbst_bytes, apply_source_section=True)
    doc.add_paragraph(APPENDIX_B_HEADING)
    append_docx_bytes(doc, support_bytes)
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def ensure_enterprise_overview_template():
    _write_trimmed_overview_docx(ENTERPRISE_OVERVIEW)
    return ENTERPRISE_OVERVIEW


def ensure_enterprise_package_template(*, use_word_com: bool = True) -> Path:
    """生成企业单文件填写模板：第 1～3 章 + 附录 A/B（高保真 Word COM 合并）。"""
    ensure_enterprise_overview_template()
    wbst_bytes = _wbst_source_path().read_bytes()
    support_bytes = _support_table_only_bytes()

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        work = Path(tmp.name)
    try:
        shutil.copy(ENTERPRISE_OVERVIEW, work)
        doc = Document(work)
        _prepend_bold_instruction(doc, PACKAGE_INSTRUCTION)
        buffer = BytesIO()
        doc.save(buffer)
        base_bytes = buffer.getvalue()

        merged = base_bytes
        if use_word_com and sys.platform == "win32":
            try:
                from apps.reports.docx_merge import _merge_appendix_parts_word

                merged = _merge_appendix_parts_word(
                    base_bytes, [(wbst_bytes, APPENDIX_A_HEADING)]
                )
                merged = _merge_appendix_parts_word(
                    merged, [(support_bytes, APPENDIX_B_HEADING)]
                )
            except Exception as exc:
                logger.warning("Word COM 生成企业报告材料模板失败，回退 python-docx：%s", exc)
                merged = _append_appendices_python(base_bytes, wbst_bytes, support_bytes)
        else:
            merged = _append_appendices_python(base_bytes, wbst_bytes, support_bytes)

        ENTERPRISE_PACKAGE.parent.mkdir(parents=True, exist_ok=True)
        ENTERPRISE_PACKAGE.write_bytes(merged)
    finally:
        work.unlink(missing_ok=True)
    return ENTERPRISE_PACKAGE


def _drop_from_heading(doc, heading_text):
    body = doc.element.body
    children = list(body)
    start = None
    for idx, child in enumerate(children):
        if child.tag != qn("w:p"):
            continue
        if _text_of(Paragraph(child, doc)) == heading_text:
            start = idx
            break
    if start is None:
        return
    sect = next((c for c in reversed(children) if c.tag == qn("w:sectPr")), None)
    for child in children[start:]:
        if child is not sect:
            body.remove(child)


def _add_table_after(paragraph, headers, rows):
    doc = paragraph._parent.part.document
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    for r, row in enumerate(rows, start=1):
        for c, val in enumerate(row):
            table.rows[r].cells[c].text = val
    paragraph._p.addnext(table._tbl)


def fill_overview_bytes(data: dict) -> bytes:
    path = ensure_enterprise_overview_template()
    doc = Document(str(path))
    proto = _find(doc, "1.1.1 任务来源")

    sections = [
        ("1.1.1 任务来源", data["s1_1_1"]),
        ("1.1.2 研究目标", data["s1_1_2"]),
        ("1.1.4 成果形式", data["s1_1_4"]),
        ("1.1.5 产品介绍及主要功能", data["s1_1_5"]),
        ("1.2.1 项目总体进展概述", data["s1_2_1"]),
        ("1.2.2 技术指标完成情况", data["s1_2_2"]),
        ("1.2.3 关键部件研制", data["s1_2_3"]),
        ("1.2.4 软件开发", data["s1_2_4"]),
        ("2.1 评价目的和范围", data["s2_1"]),
        ("2.2 评价依据", data.get("s2_2", "")),
        ("2.3 评价人员", data["s2_3"]),
        ("2.4 评价过程", data["s2_4"]),
    ]
    for title, text in sections:
        _body_after(_find(doc, title), [text], prototype=proto)

    h113 = _find(doc, "1.1.3 主要技术指标")
    if data.get("s1_1_3"):
        _body_after(h113, [data["s1_1_3"]], prototype=proto)
    if data.get("indicator_table"):
        headers, rows = data["indicator_table"]
        cap = doc.add_paragraph()
        cap.alignment = 1
        cap.add_run("表 1  主要技术指标")
        h113._p.addnext(cap._p)
        _add_table_after(cap, headers, rows)

    self_rows = data.get("self_rows")
    third_rows = data.get("third_rows")
    legacy_team = data.get("team_rows")
    if legacy_team and self_rows is None and third_rows is None:
        self_rows, third_rows = [], []
        in_third = False
        for row in legacy_team:
            label = row[0] if row else ""
            if label == "自评估工作组":
                in_third = False
                continue
            if label == "第三方评价组":
                in_third = True
                continue
            (third_rows if in_third else self_rows).append(row)
    if self_rows is not None or third_rows is not None:
        caption = _find(doc, "表X [图/表名称]")
        _set_text(caption, "表 2-1  评价组成员组成")
        el = caption._p.getnext()
        while el is not None and el.tag != qn("w:tbl"):
            el = el.getnext()
        if el is not None:
            _fill_team_table(Table(el, caption._parent), self_rows or [], third_rows or [])

    h3 = _find(doc, "3 评价对象研发信息")
    _set_text(h3, "3 大功率波长色散X荧光光谱仪研发信息")
    h31 = _find(doc, "3.1 产品描述")
    proto_h2 = h31
    _drop_from_heading(doc, "3.2 设计情况")
    last = h31
    for index, (title, body) in enumerate(data["ch3_sections"]):
        if index == 0:
            _set_text(h31, title)
            heading = h31
        else:
            p = copy.deepcopy(proto_h2._p)
            last._p.addnext(p)
            heading = Paragraph(p, doc._body)
            _set_text(heading, title)
        last = _body_after(heading, [body], prototype=proto)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()