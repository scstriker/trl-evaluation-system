"""测试用：最小企业报告材料 docx 与批量上传。"""

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from docx import Document

from apps.reports import enterprise_docs
from apps.reports.models import EnterpriseProjectFile

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def minimal_docx(*paragraphs):
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def seed_enterprise_report_files(project, user, overview_extra="企业概况独有正文XYZ"):
    for kind, name, payload in (
        (
            EnterpriseProjectFile.KIND_OVERVIEW,
            "overview.docx",
            minimal_docx("1 概况", overview_extra, "2 评价实施情况", "2.3 评价人员", "自评估工作组组长张工"),
        ),
        (EnterpriseProjectFile.KIND_SUPPORT, "support.docx", minimal_docx("附录B 评价支撑信息清单", "支撑条目A")),
        (EnterpriseProjectFile.KIND_WBST, "wbst.docx", minimal_docx("附录A WBST", "WBST单元格")),
    ):
        enterprise_docs.store_upload(
            project,
            kind,
            SimpleUploadedFile(name, payload, content_type=_DOCX),
            user,
        )