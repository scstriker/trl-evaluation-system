"""从企业上传的 Word 提取在线预览块（简化，不与 Word 版式完全一致）。"""

import re
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

MAX_PARAS = 400
MAX_TABLE_ROWS = 30


def _heading_level(style_name: str) -> int | None:
    if not style_name:
        return None
    if style_name == "Heading 1":
        return 1
    if style_name == "Heading 2":
        return 2
    if style_name in ("Heading 3", "1", "toc 3"):
        return 3
    if style_name.startswith("标题") and "1" in style_name:
        return 1
    if style_name.startswith("标题") and "2" in style_name:
        return 2
    if style_name.startswith("标题") and "3" in style_name:
        return 3
    return None


def _kind_for_level(level: int) -> str:
    return {1: "h1", 2: "h2", 3: "h3"}.get(level, "p")


def extract_blocks(file_field, *, limit_paras=MAX_PARAS) -> list[dict]:
    """从 FileField、路径或 bytes 读取 docx，转为 preview blocks。"""
    if not file_field:
        return []
    try:
        if isinstance(file_field, (bytes, bytearray)):
            data = bytes(file_field)
        elif hasattr(file_field, "open"):
            with file_field.open("rb") as f:
                data = f.read()
        else:
            data = Path(file_field).read_bytes()
    except OSError:
        return [{"kind": "p", "text": "（无法读取上传文件）"}]

    doc = Document(BytesIO(data))
    blocks: list[dict] = []
    para_count = 0
    skip_instruction = True

    for element in doc.element.body:
        if element.tag.endswith("p"):
            paragraph = Paragraph(element, doc)
            text = paragraph.text.strip()
            if not text:
                continue
            if skip_instruction and text.startswith("【填写说明】"):
                blocks.append({"kind": "p", "text": text, "muted": True})
                skip_instruction = False
                continue
            skip_instruction = False
            style = paragraph.style.name if paragraph.style else ""
            level = _heading_level(style)
            if level is None and len(text) < 80:
                if text in ("1 概况", "2 评价实施情况", "3 评价对象研发信息") or (
                    text[:1].isdigit() and " " in text and text.split(maxsplit=1)[0] in ("1", "2", "3") and len(text) < 40
                ):
                    level = 1
                elif text[:2].replace(".", "").isdigit() and text.count(".") == 1:
                    level = 2
                elif text[:3].replace(".", "").isdigit() and text.count(".") >= 2:
                    level = 3
            if level:
                blocks.append({"kind": _kind_for_level(level), "text": text})
            else:
                blocks.append({"kind": "p", "text": text})
            para_count += 1
        elif element.tag.endswith("tbl"):
            table = Table(element, doc)
            headers = [c.text.strip() for c in table.rows[0].cells] if table.rows else []
            rows = []
            for row in table.rows[1 : 1 + MAX_TABLE_ROWS]:
                rows.append([c.text.strip() for c in row.cells])
            if headers or rows:
                blocks.append({"kind": "table", "headers": headers or ["列1"], "rows": rows or [["—"]]})
        if para_count >= limit_paras:
            blocks.append({"kind": "p", "text": "（正文较长，此处仅预览前几页；完整内容见出具 Word。）", "muted": True})
            break
    return blocks


_APPENDIX_HEADING = re.compile(r"^(附录|[0-9]+\s+附录)")


def extract_blocks_from_appendix(file_field, *, limit_paras=40) -> list[dict]:
    """从单文件企业材料中截取附录段预览（定位首个附录标题后）。"""
    blocks = extract_blocks(file_field)
    if not blocks:
        return []
    start = None
    for index, block in enumerate(blocks):
        text = (block.get("text") or "").strip()
        if _APPENDIX_HEADING.match(text) or text.startswith("附录A") or text.startswith("附录B"):
            start = index
            break
    if start is None:
        return []
    return blocks[start : start + limit_paras]


def appendix_status_block(kind_label: str, filename: str) -> list[dict]:
    return [
        {"kind": "h2", "text": kind_label},
        {"kind": "p", "text": f"已上传「{filename}」，生成正式报告时将并入 Word。"},
    ]