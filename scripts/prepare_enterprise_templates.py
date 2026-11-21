"""生成企业填写用 Word 模板（概况沿用机构原版式，单文件企业报告材料含附录 A/B）。"""

from pathlib import Path

from docx import Document
from docx.shared import Pt

BASE = Path(__file__).resolve().parent.parent
ENT_DIR = BASE / "data" / "templates" / "enterprise"

INSTRUCTION_SUPPORT = (
    "【填写说明】请按评价支撑信息清单填写附录 B 内容（相关文件、关联细则等）。"
    "填写完成后在系统上传「评价支撑信息」。"
)


def build_support_template():
    doc = Document()
    p = doc.add_paragraph(INSTRUCTION_SUPPORT)
    p.runs[0].bold = True
    p.runs[0].font.size = Pt(10.5)
    doc.add_paragraph()
    doc.add_paragraph("附录B  评价支撑信息清单")
    table = doc.add_table(rows=2, cols=4)
    for i, h in enumerate(["编号", "相关文件（佐证材料）", "关联细则", "备注"]):
        table.rows[0].cells[i].text = h
    table.rows[1].cells[0].text = "（示例）"
    path = ENT_DIR / "评价支撑信息-模板.docx"
    doc.save(path)
    print(f"已生成：{path}")


if __name__ == "__main__":
    import os
    import sys

    import django

    sys.path.insert(0, str(BASE))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    sys.path.insert(0, str(BASE / "scripts"))
    from comparison_content import warm_enterprise_template

    warm_enterprise_template()
    print(f"已生成：{ENT_DIR / '项目概况-模板.docx'}（过渡）")
    print(f"已生成：{ENT_DIR / '企业报告材料-模板.docx'}")
    build_support_template()