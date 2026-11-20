"""Word 文档合并：企业上传材料 + 系统生成章节。"""

import copy
import logging
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)

SYSTEM_CHAPTER_MARKERS = (
    "4 评价准则",
    "5 技术就绪度评价",
    "5 制造成熟度评价",
    "6 总结",
    "7 附录",
)


def _paragraph_text(element, parent):
    if element.tag != qn("w:p"):
        return ""
    return "".join(run.text for run in Paragraph(element, parent).runs).strip()


def validate_overview_docx(path_or_bytes):
    """企业概况不得包含系统生成的章节标题。"""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        doc = Document(BytesIO(path_or_bytes))
    else:
        doc = Document(str(path_or_bytes))
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        for marker in SYSTEM_CHAPTER_MARKERS:
            if text == marker or text.startswith(marker):
                raise ValueError(f"项目概况 Word 中不应包含「{marker}」，请仅填写第 1～3 章。")
    return True


def trim_template_front_matter(document):
    """删除已渲染模板中从「1 概况」起的正文，保留封面与扉页，供插入企业 Word。"""
    body = document.element.body
    children = list(body)
    start = None
    for idx, child in enumerate(children):
        if child.tag != qn("w:p"):
            continue
        text = _paragraph_text(child, document)
        if text == "1 概况" or text.startswith("1 概况"):
            start = idx
            break
    if start is None:
        return
    sect = None
    for child in reversed(children):
        if child.tag == qn("w:sectPr"):
            sect = child
            break
    for child in children[start:]:
        if child is sect:
            continue
        body.remove(child)


def _append_body_elements(target_doc, source_doc, skip_before_heading=None):
    """将 source 正文元素追加到 target（不含 sectPr）。"""
    body = target_doc.element.body
    sect = None
    for child in list(body):
        if child.tag == qn("w:sectPr"):
            sect = child
    started = skip_before_heading is None
    for child in list(source_doc.element.body):
        if child.tag == qn("w:sectPr"):
            continue
        if not started and child.tag == qn("w:p"):
            text = _paragraph_text(child, source_doc)
            if text.startswith(skip_before_heading or ""):
                started = True
            else:
                continue
        if not started:
            continue
        body.insert(body.index(sect) if sect is not None else len(body), copy.deepcopy(child))


def append_docx_file(target_doc, path, skip_before_heading=None):
    if not Path(path).is_file():
        return
    source = Document(str(path))
    _append_body_elements(target_doc, source, skip_before_heading=skip_before_heading)


def append_docx_bytes(target_doc, data: bytes, skip_before_heading=None):
    source = Document(BytesIO(data))
    _append_body_elements(target_doc, source, skip_before_heading=skip_before_heading)


def merge_overview_com(shell_bytes: bytes, overview_bytes: bytes) -> bytes:
    """用 Word 在壳层文末插入企业概况，避免跨文档合并时样式 ID 错位（版式与金标准一致）。"""
    return _merge_appendix_parts_word(shell_bytes, [(overview_bytes, None)])


def _body_section_start_index(doc) -> int:
    """返回包含「1 概况」的节序号（其后页眉应为项目名称，而非「目录」）。"""
    sec_idx = 0
    for paragraph in doc.paragraphs:
        p_pr = paragraph._p.pPr
        if p_pr is not None and p_pr.sectPr is not None:
            sec_idx += 1
        if paragraph.text.strip() in ("1 概况", "1  概况") or paragraph.text.strip().startswith("1 概况"):
            return sec_idx
    return min(1, len(doc.sections))


def fix_body_section_headers(docx_bytes: bytes, header_line: str) -> bytes:
    """Word InsertFile 后常把正文节页眉落成「目录」；从第 1 章起恢复为报告页眉。"""
    if not header_line:
        return docx_bytes
    doc = Document(BytesIO(docx_bytes))
    start = _body_section_start_index(doc)
    for section in doc.sections[start:]:
        header = section.header
        if not header.paragraphs:
            header.add_paragraph(header_line)
            continue
        for paragraph in header.paragraphs:
            paragraph.text = header_line
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def merge_with_word_com(base_bytes: bytes, inserts: list[tuple[bytes, str | None]]) -> bytes:
    """Windows Word COM：在文末依次 InsertFile。inserts: [(bytes, optional_heading)]."""
    if sys.platform != "win32":
        raise OSError("Word COM 仅适用于 Windows")
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "base.docx"
            base_path.write_bytes(base_bytes)
            doc = word.Documents.Open(str(base_path), ReadOnly=False, AddToRecentFiles=False, Visible=False)
            try:
                rng = doc.Content
                rng.Collapse(0)  # wdCollapseEnd
                for payload, heading in inserts:
                    if heading:
                        rng.InsertAfter("\r" + heading + "\r")
                        rng.Collapse(0)
                    part_path = Path(tmp) / "part.docx"
                    part_path.write_bytes(payload)
                    rng.InsertFile(str(part_path))
                    rng.Collapse(0)
                out_path = Path(tmp) / "merged.docx"
                doc.SaveAs2(str(out_path), FileFormat=16)
                return out_path.read_bytes()
            finally:
                doc.Close(False)
    finally:
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()


def append_appendices_only(base_bytes: bytes, wbst_bytes: bytes | None, support_bytes: bytes | None, use_word_com: bool = True) -> bytes:
    """在已含正文的 docx 末尾追加第 7 章附录及 WBST、评价支撑信息文件内容。"""
    appendix_parts: list[tuple[bytes | None, str | None]] = [(None, "7 附录")]
    if wbst_bytes:
        appendix_parts.append((wbst_bytes, "附录A  WBST表格"))
    if support_bytes:
        appendix_parts.append((support_bytes, "附录B  评价支撑信息清单"))
    if len(appendix_parts) == 1:
        return base_bytes

    if use_word_com and sys.platform == "win32":
        try:
            return _merge_appendix_parts_word(base_bytes, appendix_parts)
        except Exception as exc:
            logger.warning("Word COM 合并附录失败，回退 python-docx：%s", exc)

    doc = Document(BytesIO(base_bytes))
    for payload, heading in appendix_parts:
        if heading:
            doc.add_paragraph(heading)
        if payload:
            append_docx_bytes(doc, payload)
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _merge_appendix_parts_word(base_bytes: bytes, parts: list[tuple[bytes | None, str | None]]) -> bytes:
    """COM 合并：支持仅标题或 InsertFile。"""
    if sys.platform != "win32":
        raise OSError("Word COM 仅适用于 Windows")
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "base.docx"
            base_path.write_bytes(base_bytes)
            doc = word.Documents.Open(str(base_path), ReadOnly=False, AddToRecentFiles=False, Visible=False)
            try:
                rng = doc.Content
                rng.Collapse(0)
                for payload, heading in parts:
                    if heading:
                        rng.InsertAfter("\r" + heading + "\r")
                        rng.Collapse(0)
                    if payload:
                        part_path = Path(tmp) / "part.docx"
                        part_path.write_bytes(payload)
                        rng.InsertFile(str(part_path))
                        rng.Collapse(0)
                out_path = Path(tmp) / "merged.docx"
                doc.SaveAs2(str(out_path), FileFormat=16)
                return out_path.read_bytes()
            finally:
                doc.Close(False)
    finally:
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()