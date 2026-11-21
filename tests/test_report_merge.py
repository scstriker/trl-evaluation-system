"""企业模板上传与报告 Word 合并。"""

from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from docx import Document
from PIL import Image

from apps.reports import enterprise_docs
from apps.reports import services as report_services
from apps.reports.models import EnterpriseProjectFile, Report
from apps.reports import docx_merge
from tests.report_enterprise_fixtures import minimal_docx, seed_enterprise_report_files
from tests.test_reports import _docx_text
from tests.test_round2 import _ready_project


def test_request_blockers_require_enterprise_uploads(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user, seed_enterprise=False)
    blockers = report_services.request_blockers(project, Report.TYPE_TRL)
    assert report_services.OVERVIEW_MISSING in blockers
    assert report_services.WBST_MISSING in blockers
    assert report_services.SUPPORT_MISSING in blockers
    seed_enterprise_report_files(project, enterprise)
    blockers = report_services.request_blockers(project, Report.TYPE_TRL)
    assert report_services.OVERVIEW_MISSING not in blockers


def test_preview_updates_after_overview_upload(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user, seed_enterprise=False)
    before = report_services.build_report(project, Report.TYPE_TRL)
    assert any("待企业下载" in b.get("text", "") for b in before["blocks"] if b.get("kind") == "p")
    seed_enterprise_report_files(project, enterprise, overview_extra="上传后应出现在预览")
    after = report_services.build_report(project, Report.TYPE_TRL)
    assert after["merge_mode"]
    assert any("上传后应出现在预览" in b.get("text", "") for b in after["blocks"])


def test_enterprise_upload_rejects_non_docx(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user, seed_enterprise=False)
    pdf = SimpleUploadedFile("wbst.pdf", b"%PDF-1.4", content_type="application/pdf")
    with pytest.raises(ValidationError, match="须上传 .docx"):
        enterprise_docs.store_upload(project, EnterpriseProjectFile.KIND_WBST, pdf, enterprise)


def test_append_docx_preserves_inline_image():
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.shared import Inches

    buf = BytesIO()
    Image.new("RGB", (8, 8), color="red").save(buf, format="PNG")
    buf.seek(0)
    source = Document()
    source.add_paragraph("证据截图")
    source.add_picture(buf, width=Inches(0.2))
    src_bytes = BytesIO()
    source.save(src_bytes)
    target = Document()
    target.add_paragraph("壳层")
    docx_merge.append_docx_bytes(target, src_bytes.getvalue())
    out = BytesIO()
    target.save(out)
    merged = Document(out)
    assert "证据截图" in "\n".join(p.text for p in merged.paragraphs)
    assert any(rel.reltype == RT.IMAGE for rel in merged.part.rels.values())


def test_appendix_chapter_matches_report_depth(enterprise, admin_user):
    from apps.reports.models import Report
    from apps.reports.services import appendix_chapter_number, build_report

    project = _ready_project(enterprise, admin_user, seed_enterprise=True)
    trl = build_report(project, Report.TYPE_TRL)
    assert trl["appendix_chapter"] == 7
    assert trl["appendix_preview_blocks"][0]["text"] == "7 附录"
    assert appendix_chapter_number(1) == 7
    assert appendix_chapter_number(2) == 8


def test_overview_upload_rejects_system_chapter():
    bad = minimal_docx("1 概况", "4 评价准则")
    with pytest.raises(ValueError, match="不应包含"):
        docx_merge.validate_overview_docx(bad)


def test_generated_report_merges_enterprise_overview(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user)
    seed_enterprise_report_files(project, enterprise)
    record = report_services.generate_report(project, Report.TYPE_TRL, admin_user)
    text = _docx_text(record)
    assert "企业概况独有正文XYZ" in text
    assert "4 评价准则" in text
    assert record.overview_file_id is not None
    assert record.support_file_id is not None
    assert record.wbst_file_id is not None