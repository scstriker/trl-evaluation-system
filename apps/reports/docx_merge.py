"""Word 文档合并：企业上传材料 + 系统生成章节。"""

import copy
import logging
import re
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
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


def _is_system_chapter_heading(text: str) -> bool:
    for marker in SYSTEM_CHAPTER_MARKERS:
        if text == marker or text.startswith(marker):
            return True
    if re.match(r"^[4-9]\s+附录", text):
        return True
    return False


def validate_overview_docx(path_or_bytes):
    """企业概况不得包含系统生成的章节标题。"""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        doc = Document(BytesIO(path_or_bytes))
    else:
        doc = Document(str(path_or_bytes))
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if _is_system_chapter_heading(text):
            raise ValueError(f"项目概况 Word 中不应包含「{text}」，请仅填写第 1～3 章。")
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


_REL_ID_ATTRS = (qn("r:embed"), qn("r:id"))


def _collect_embed_ids(element, ids):
    for attr in _REL_ID_ATTRS:
        value = element.get(attr)
        if value:
            ids.add(value)
    for child in element:
        _collect_embed_ids(child, ids)


def _remap_embed_ids(element, id_map):
    for attr in _REL_ID_ATTRS:
        old = element.get(attr)
        if old and old in id_map:
            element.set(attr, id_map[old])
    for child in element:
        _remap_embed_ids(child, id_map)


def _part_rel_map(target_doc, source_doc, needed_ids):
    """将 source 中被引用的部件关系复制到 target，避免 rId 与目标壳层错配。"""
    mapping = {}
    for r_id in needed_ids:
        rel = source_doc.part.rels.get(r_id)
        if rel is None:
            continue
        try:
            if rel.is_external:
                mapping[r_id] = target_doc.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
            else:
                mapping[r_id] = target_doc.part.relate_to(rel.target_part, rel.reltype)
        except Exception as exc:  # noqa: BLE001
            logger.warning("复制 Word 关系 %s 失败：%s", r_id, exc)
    return mapping


def _trailing_sect_pr(source_doc):
    for child in reversed(list(source_doc.element.body)):
        if child.tag == qn("w:sectPr"):
            return copy.deepcopy(child)
        if child.tag == qn("w:p"):
            p_pr = child.find(qn("w:pPr"))
            if p_pr is not None:
                sect = p_pr.find(qn("w:sectPr"))
                if sect is not None:
                    return copy.deepcopy(sect)
    return None


def _section_break_paragraph(sect_pr):
    paragraph = OxmlElement("w:p")
    p_pr = OxmlElement("w:pPr")
    p_pr.append(sect_pr)
    paragraph.append(p_pr)
    return paragraph


def _append_body_elements(
    target_doc,
    source_doc,
    skip_before_heading=None,
    *,
    apply_source_section=False,
):
    """将 source 正文元素追加到 target（不含末尾 sectPr），并复制关系与分节。"""
    body = target_doc.element.body
    sect = None
    for child in list(body):
        if child.tag == qn("w:sectPr"):
            sect = child
    insert_at = body.index(sect) if sect is not None else len(body)
    if apply_source_section:
        source_sect = _trailing_sect_pr(source_doc)
        if source_sect is not None:
            body.insert(insert_at, _section_break_paragraph(source_sect))
            insert_at += 1
    started = skip_before_heading is None
    to_insert = []
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
        clone = copy.deepcopy(child)
        needed = set()
        _collect_embed_ids(clone, needed)
        _remap_embed_ids(clone, _part_rel_map(target_doc, source_doc, needed))
        to_insert.append(clone)
    for clone in to_insert:
        body.insert(insert_at, clone)
        insert_at += 1


def append_docx_file(
    target_doc,
    path,
    skip_before_heading=None,
    *,
    apply_source_section=False,
):
    if not Path(path).is_file():
        return
    source = Document(str(path))
    _append_body_elements(
        target_doc,
        source,
        skip_before_heading=skip_before_heading,
        apply_source_section=apply_source_section,
    )


def append_docx_bytes(
    target_doc,
    data: bytes,
    skip_before_heading=None,
    *,
    apply_source_section=False,
):
    source = Document(BytesIO(data))
    _append_body_elements(
        target_doc,
        source,
        skip_before_heading=skip_before_heading,
        apply_source_section=apply_source_section,
    )


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


def append_appendices_only(
    base_bytes: bytes,
    wbst_bytes: bytes | None,
    support_bytes: bytes | None,
    *,
    appendix_chapter: int = 7,
    use_word_com: bool = True,
) -> bytes:
    """在已含正文的 docx 末尾追附录章及 WBST、评价支撑信息文件内容。"""
    appendix_parts: list[tuple[bytes | None, str | None]] = [(None, f"{appendix_chapter} 附录")]
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
            append_docx_bytes(doc, payload, apply_source_section=payload is wbst_bytes)
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