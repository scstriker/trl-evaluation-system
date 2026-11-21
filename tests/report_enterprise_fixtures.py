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


def package_docx(overview_extra="企业概况独有正文XYZ", wbst_note="WBST单元格", support_note="支撑条目A"):
    return minimal_docx(
        "1 概况",
        overview_extra,
        "2 评价实施情况",
        "2.3 评价人员",
        "自评估工作组组长张工",
        "附录A  WBST表格",
        wbst_note,
        "附录B  评价支撑信息清单",
        support_note,
    )


def seed_enterprise_report_files(project, user, overview_extra="企业概况独有正文XYZ"):
    payload = package_docx(overview_extra=overview_extra)
    enterprise_docs.store_upload(
        project,
        EnterpriseProjectFile.KIND_PACKAGE,
        SimpleUploadedFile("package.docx", payload, content_type=_DOCX),
        user,
    )