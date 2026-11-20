"""第三轮：按固定模板生成报告（前置信息、系统章节拼接、印章、打开时刷新目录、PDF 转换）。"""

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from docx import Document
from docx.oxml.ns import qn
from docxtpl import DocxTemplate

from apps.accounts.models import SystemConfig
from apps.reports import docx_render, pdf
from apps.reports import services as report_services
from apps.reports.models import Report, ReportModule
from apps.rules.systems import TRL
from tests.test_reports import _docx_text
from tests.test_round2 import _ready_project


def _png():
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGBA", (120, 120), (200, 20, 20, 200)).save(buffer, "PNG")
    return buffer.getvalue()


def _fill_front(project):
    front = report_services.get_front_matter(project)
    front.compiled_by, front.checked_by, front.approved_by = "编制甲", "审核乙", "批准丙"
    front.s1_1_1 = "任务来源第一段\n任务来源第二段"
    front.s1_1_5 = "产品介绍 <含尖括号> & 符号"
    front.s3_4_software = "软件测评通过"
    front.standard_version = "《技术就绪度评价标准及细则》（2026 年版）"
    front.save()
    ReportModule.objects.create(front_matter=front, order=1, name="关键模块甲", design="甲设计", trial="甲试制", verification="甲验证")
    ReportModule.objects.create(front_matter=front, order=2, name="关键模块乙", design="乙设计")
    return front


def test_template_variables_are_all_provided(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user)
    report = report_services.build_report(project, Report.TYPE_TRL)
    context = docx_render.template_context(report["front"], "NO-1")
    template = DocxTemplate(str(docx_render.TEMPLATE_PATH))
    missing = template.get_undeclared_template_variables(context=context)
    assert not missing, missing


def test_generated_word_follows_template(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user)
    _fill_front(project)
    record = report_services.generate_report(project, Report.TYPE_TRL, admin_user)
    assert not record.pdf_file  # 测试中关闭了 PDF 转换
    document = Document(BytesIO(record.file.read()))
    record.file.seek(0)
    text = _docx_text(record)
    # 封面、扉页
    assert "委托单位：甲企业有限公司" in text and "评价报告出具单位：" in text and "二〇" in text
    assert "编制甲" in text and "审核乙" in text and "批准丙" in text
    assert "（盖章）" in text and docx_render.SEAL_MARK not in text
    # 企业概况 Word 与壳层、系统章节拼接
    assert "企业概况独有正文XYZ" in text
    # 第 4 章起由系统拼接；模板标签都已替换
    assert "4 评价准则" in text and "5 技术就绪度评价" in text
    assert "7 附录" in text or "附录A" in text
    assert "{{" not in text and "{%" not in text
    # 页眉
    headers = " ".join(p.text for s in document.sections for p in s.header.paragraphs)
    assert f"{project.name}技术就绪度评价" in headers
    # 打开时刷新目录
    assert document.settings.element.find(qn("w:updateFields")) is not None


def test_seal_image_is_floating(enterprise, admin_user, client):
    project = _ready_project(enterprise, admin_user)
    client.force_login(admin_user)
    client.post(
        "/settings/",
        {
            "expert_review_days": "7",
            "rectify_days": "14",
            "expert_fee_default": "0",
            "seal_upload": SimpleUploadedFile("seal.png", _png(), content_type="image/png"),
        },
    )
    assert SystemConfig.get().seal_image
    record = report_services.generate_report(project, Report.TYPE_TRL, admin_user)
    document = Document(BytesIO(record.file.read()))
    anchors = document.element.body.findall(".//" + qn("wp:anchor"))
    assert len(anchors) == 1
    record.file.seek(0)
    assert "（盖章）" not in _docx_text(record)


def test_preview_blocks_match_template_structure(client, enterprise, admin_user):
    from tests.report_enterprise_fixtures import seed_enterprise_report_files

    project = _ready_project(enterprise, admin_user, seed_enterprise=False)
    report = report_services.build_report(project, Report.TYPE_TRL)
    assert any("待企业下载" in b.get("text", "") for b in report["blocks"])
    seed_enterprise_report_files(project, enterprise, overview_extra="预览可见正文片段")
    report = report_services.build_report(project, Report.TYPE_TRL)
    texts = [b.get("text", "") for b in report["blocks"]]
    assert "1 概况" in texts and "预览可见正文片段" in texts and "4 评价准则" in texts
    client.force_login(enterprise)
    page = client.get(f"/reports/projects/{project.id}/").content.decode()
    assert "预览可见正文片段" in page and "编制甲" not in page


def test_front_matter_page(client, enterprise, admin_user):
    project = _ready_project(enterprise, admin_user)
    client.force_login(enterprise)
    assert client.get(f"/reports/projects/{project.id}/front-matter/").status_code == 403
    client.force_login(admin_user)
    page = client.get(f"/reports/projects/{project.id}/front-matter/").content.decode()
    assert "1.1.1 任务来源" in page and "留空时使用" in page
    data = {
        "type": "trl",
        "compiled_by": "张三",
        "checked_by": "",
        "approved_by": "",
        "issue_date": "2026-10-20",
        "standard_version": "",
        "s1_1_1": "来源说明",
        "modules-TOTAL_FORMS": "1",
        "modules-INITIAL_FORMS": "0",
        "modules-MIN_NUM_FORMS": "0",
        "modules-MAX_NUM_FORMS": "1000",
        "modules-0-name": "新模块",
        "modules-0-design": "设计说明",
        "modules-0-trial": "",
        "modules-0-verification": "",
    }
    for field in report_services.ReportFrontMatter.SECTION_FIELDS:
        data.setdefault(field, "")
    response = client.post(f"/reports/projects/{project.id}/front-matter/", data)
    assert response.status_code == 302
    front = report_services.get_front_matter(project)
    assert front.compiled_by == "张三" and str(front.issue_date) == "2026-10-20" and front.s1_1_1 == "来源说明"
    assert list(front.modules.values_list("name", flat=True)) == ["新模块"]
    report = report_services.build_report(project, Report.TYPE_TRL)
    assert report["front"]["issue_date_cn"] == "二〇二六年十月"


def test_pdf_detection_respects_setting(settings):
    settings.REPORT_PDF_CONVERTER = "none"
    assert pdf.detect_converter() is None
    with pytest.raises(pdf.PdfError):
        pdf.convert(b"not a docx")


def test_make_pdf_without_converter_shows_error(client, enterprise, admin_user):
    project = _ready_project(enterprise, admin_user)
    record = report_services.generate_report(project, Report.TYPE_TRL, admin_user)
    client.force_login(admin_user)
    client.post(f"/reports/{record.id}/make-pdf/")
    record.refresh_from_db()
    assert not record.pdf_file
    assert client.get(f"/reports/{record.id}/download.pdf").status_code == 404


@pytest.mark.skipif(not pdf._word_installed(), reason="本机没有 Microsoft Word")
def test_real_word_conversion(settings, enterprise, admin_user, client):
    settings.REPORT_PDF_CONVERTER = "word"
    project = _ready_project(enterprise, admin_user)
    _fill_front(project)
    record = report_services.generate_report(project, Report.TYPE_TRL, admin_user)
    assert record.pdf_converter == "word" and record.pdf_sha256
    assert record.pdf_file.read(5) == b"%PDF-"
    # Word 已刷新目录，交付的 Word 不再要求打开时更新域
    document = Document(BytesIO(record.file.read()))
    assert document.settings.element.find(qn("w:updateFields")) is None
    client.force_login(enterprise)
    response = client.get(f"/reports/{record.id}/download.pdf")
    assert response.status_code == 200 and response["Content-Disposition"].endswith(".pdf")
