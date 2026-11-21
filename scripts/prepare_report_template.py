"""把评价机构提供的《技术就绪度评价报告_原版式空白模板》加工成可填充的报告模板（docxtpl / Jinja 标签）。

用法（在 trl-evaluation-system 目录下）：
    python scripts/prepare_report_template.py                     # 默认读 data/templates/report/source/ 下的原版模板
    python scripts/prepare_report_template.py 原版模板.docx 输出.docx

处理方式：按文字定位段落（不依赖段落序号），保留原版式的样式、页眉页脚、分节和目录域，
只把“[填写]”类占位换成标签、在各章节标题下插入正文循环、把评价组表和评价依据表改成按行循环。
模板改版后重新运行本脚本即可。生成结果供 apps/reports/docx_render.py 使用。
"""

import copy
import sys
from pathlib import Path

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.table import Table
from docx.text.paragraph import Paragraph

BASE = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = BASE / "data" / "templates" / "report" / "source" / "技术就绪度评价报告_原版式空白模板.docx"
DEFAULT_OUTPUT = BASE / "data" / "templates" / "report" / "report_template.docx"

# 标题文字 → 标题下的正文字段
SECTION_FIELDS = {
    "1.1.1 任务来源": "s1_1_1",
    "1.1.2 研究目标": "s1_1_2",
    "1.1.3 主要技术指标": "s1_1_3",
    "1.1.4 成果形式": "s1_1_4",
    "1.1.5 产品介绍及主要功能": "s1_1_5",
    "1.2.1 项目总体进展概述": "s1_2_1",
    "1.2.2 技术指标完成情况": "s1_2_2",
    "1.2.3 关键部件研制": "s1_2_3",
    "1.2.4 软件开发": "s1_2_4",
    "2.1 评价目的和范围": "s2_1",
    "3.1 产品描述": "s3_1",
}

# 正文段落格式：两端对齐、首行缩进 2 字、1.5 倍行距
BODY_PPR = (
    f'<w:pPr {nsdecls("w")}><w:spacing w:line="360" w:lineRule="auto"/>'
    '<w:ind w:firstLineChars="200" w:firstLine="480"/><w:jc w:val="both"/></w:pPr>'
)


def text_of(paragraph):
    return "".join(run.text for run in paragraph.runs).strip()


def set_text(paragraph, text):
    """保留第一个文字段（run）的格式，替换整段文字。"""
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return
    runs[0].text = text
    for run in runs[1:]:
        run._r.getparent().remove(run._r)


def new_paragraph_after(element, text, body=False, like=None):
    """在 element 之后插入一个段落：like 给出要复制格式的段落，body=True 用正文格式。"""
    if like is not None:
        p = copy.deepcopy(like._p)
        for child in list(p):
            if child.tag != qn("w:pPr"):
                p.remove(child)
    else:
        p = parse_xml(f'<w:p {nsdecls("w")}/>')
        if body:
            p.append(parse_xml(BODY_PPR))
    element.addnext(p)
    paragraph = Paragraph(p, None)
    if like is not None and like.runs:
        run = copy.deepcopy(like.runs[0]._r)
        for child in list(run):
            if child.tag != qn("w:rPr"):
                run.remove(child)
        p.append(run)
        paragraph.runs[0].text = text
    else:
        paragraph.add_run(text)
    return paragraph


def insert_loop(after_element, var, field):
    """在 after_element 之后插入：{%p for var in field %} / {{ var }} / {%p endfor %}，返回最后一个段落。"""
    first = new_paragraph_after(after_element, f"{{%p for {var} in {field} %}}")
    body = new_paragraph_after(first._p, f"{{{{ {var} }}}}", body=True)
    return new_paragraph_after(body._p, "{%p endfor %}")


def remove(paragraph):
    paragraph._p.getparent().remove(paragraph._p)


def paragraphs(doc):
    return [Paragraph(el, doc._body) for el in doc.element.body.iterchildren(qn("w:p"))]


def find(doc, text, start=0):
    for paragraph in paragraphs(doc)[start:]:
        if text_of(paragraph) == text:
            return paragraph
    raise SystemExit(f"模板中找不到段落：{text}")


def following_table(paragraph):
    element = paragraph._p.getnext()
    while element is not None and element.tag != qn("w:tbl"):
        element = element.getnext()
    if element is None:
        raise SystemExit(f"“{text_of(paragraph)}”之后没有表格")
    return Table(element, paragraph._parent)


def set_cell(cell, text):
    for extra in cell.paragraphs[1:]:
        extra._p.getparent().remove(extra._p)
    set_text(cell.paragraphs[0], text)


def strip_vmerge(row):
    for tc_pr in row._tr.iter(qn("w:tcPr")):
        for vmerge in tc_pr.findall(qn("w:vMerge")):
            tc_pr.remove(vmerge)


def loop_rows(table, first_index, count, loop_var, rows_var, fields):
    """把 table 中从 first_index 起的 count 行替换为三行：{%tr for %}、模板行、{%tr endfor %}。"""
    rows = list(table.rows)
    template_row = rows[first_index]
    strip_vmerge(template_row)
    for_row = copy.deepcopy(template_row._tr)
    end_row = copy.deepcopy(template_row._tr)
    template_row._tr.addprevious(for_row)
    template_row._tr.addnext(end_row)
    for row in rows[first_index + 1 : first_index + count]:
        row._tr.getparent().remove(row._tr)
    from docx.table import _Row

    for_cells = _Row(for_row, table).cells
    end_cells = _Row(end_row, table).cells
    for index, cell in enumerate(for_cells):
        set_cell(cell, f"{{%tr for {loop_var} in {rows_var} %}}" if index == 0 else "")
    for index, cell in enumerate(end_cells):
        set_cell(cell, "{%tr endfor %}" if index == 0 else "")
    for cell, field in zip(template_row.cells, fields):
        set_cell(cell, f"{{{{ {loop_var}.{field} }}}}")


def module_loop(doc, heading_prefix, first_text, second_text, attr):
    """3.2 / 3.3 / 3.4：把“关键技术/模块 1、2 ……”换成按模块循环的标题和正文。返回循环结束段落。"""
    first = find(doc, first_text)
    second = find(doc, second_text)
    ellipsis = None
    element = second._p.getnext()
    if element is not None and Paragraph(element, doc._body).text.strip() == "……":
        ellipsis = Paragraph(element, doc._body)
    start = new_paragraph_after(first._p.getprevious(), "{%p for m in modules %}")
    heading = new_paragraph_after(start._p, f"{heading_prefix}.{{{{ loop.index }}}} {{{{ m.name }}}}{attr[1]}", like=first)
    end = insert_loop(heading._p, "para", f"m.{attr[0]}")
    close = new_paragraph_after(end._p, "{%p endfor %}")
    remove(first)
    remove(second)
    if ellipsis is not None:
        remove(ellipsis)
    return close, heading


def prepare(source, output):
    doc = Document(source)

    # ---- 封面：报告编号、标题、项目名称、委托单位 / 出具单位 / 日期 ----
    cover_table = doc.tables[0]
    set_cell(cover_table.cell(0, 1), "{{ report_no }}")
    for paragraph in paragraphs(doc):
        text = text_of(paragraph)
        if text == "技术就绪度评价报告":
            set_text(paragraph, "{{ report_title }}")
        elif text == "[项目/产品名称]":
            set_text(paragraph, "{{ project_name }}")
    placeholders = [p for p in paragraphs(doc) if text_of(p) == "[填写单位或日期]"]
    if len(placeholders) != 5:
        raise SystemExit(f"预期 5 处“[填写单位或日期]”，实际 {len(placeholders)} 处")
    for paragraph, text in zip(
        placeholders, ["委托单位：{{ applicant }}", "评价报告出具单位：{{ issuer }}", "{{ issue_date_cn }}", "{{ issuer }}", "{{ issue_date_cn }}"]
    ):
        set_text(paragraph, text)

    # ---- 扉页：编制 / 审核 / 批准；印章放在出具单位上一行（生成时改为浮动图片压在单位和日期上） ----
    for label, var in (("编制：", "compiled_by"), ("审核：", "checked_by"), ("批准：", "approved_by")):
        paragraph = next(p for p in paragraphs(doc) if text_of(p).startswith(label) and "[填写]" in text_of(p))
        runs = paragraph.runs
        runs[1].text = runs[1].text.replace("[", "") + f"{{{{ {var} }}}}"
        for run in runs[2:]:
            run._r.getparent().remove(run._r)
    seal_anchor = placeholders[3]._p.getprevious()
    seal = Paragraph(seal_anchor, doc._body)
    set_text(seal, "{{ seal }}")
    seal.alignment = 1  # 居中

    # ---- 页眉：“[项目/技术名称]技术就绪度评价” ----
    for section in doc.sections:
        for paragraph in section.header.paragraphs:
            if "[项目/技术名称]" in paragraph.text:
                set_text(paragraph, "{{ project_name }}{{ header_suffix }}")

    # ---- 1 概况 / 2.1 / 3.1：标题下插入正文 ----
    for heading_text, field in SECTION_FIELDS.items():
        heading = find(doc, heading_text)
        insert_loop(heading._p, "para", field)

    # ---- 2.2 评价依据：表格按行循环 ----
    basis_table = following_table(find(doc, "2.2 评价依据"))
    loop_rows(basis_table, 1, len(basis_table.rows) - 1, "b", "basis", ["code", "name"])

    # ---- 2.3 评价人员：引导语、表题、两组成员按行循环 ----
    heading = find(doc, "2.3 评价人员")
    intro = Paragraph(heading._p.getnext(), doc._body)
    end = insert_loop(heading._p, "para", "s2_3")
    if not intro.text.strip(" 　"):
        remove(intro)
    caption = find(doc, "表X [图/表名称]")
    set_text(caption, "{{ team_caption }}")
    team_table = following_table(caption)
    rows = team_table.rows
    third_start = next(i for i, row in enumerate(rows) if row.cells[0].text.strip() == "第三方评价组")
    fields = ["role", "name", "title", "specialty", "org"]
    loop_rows(team_table, third_start + 2, len(rows) - third_start - 2, "m", "third_rows", fields)
    loop_rows(team_table, 2, third_start - 2, "m", "self_rows", fields)

    # ---- 2.4 评价过程 ----
    heading = find(doc, "2.4 评价过程")
    placeholder = Paragraph(heading._p.getnext(), doc._body)
    insert_loop(heading._p, "para", "s2_4")
    if not placeholder.text.strip(" 　"):
        remove(placeholder)

    # ---- 3.2 / 3.3 / 3.4：按关键技术 / 模块循环 ----
    module_loop(doc, "3.2", "3.2.1 关键技术/模块1设计情况", "3.2.2 关键技术/模块2设计情况", ("design", "设计情况"))
    module_loop(doc, "3.3", "3.3.1 关键技术/模块1试制情况", "3.3.2 关键技术/模块2试制情况", ("trial", "试制情况"))
    close, heading = module_loop(doc, "3.4", "3.4.1 关键技术/模块1验证情况", "3.4.2 关键技术/模块2验证情况", ("verification", "验证情况"))
    software = next(p for p in paragraphs(doc) if text_of(p).startswith("3.4.") and "软件验证情况" in text_of(p))
    tail = software._p.getnext()
    if tail is not None and Paragraph(tail, doc._body).text.strip() == "……":
        remove(Paragraph(tail, doc._body))
    if_start = new_paragraph_after(close._p, "{%p if s3_4_software %}")
    software_heading = new_paragraph_after(if_start._p, "3.4.{{ modules|length + 1 }} 软件验证情况", like=software)
    end = insert_loop(software_heading._p, "para", "s3_4_software")
    new_paragraph_after(end._p, "{%p endif %}")
    remove(software)

    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    print(f"已生成报告模板：{output}")


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT
    prepare(source, output)
