import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.evaluations import services as eval_services
from apps.evaluations.models import EvaluationProject
from apps.evidence.services import upload_evidence_file
from apps.evidence.models import EvidenceFile
from apps.reports import enterprise_docs
from apps.reports.models import EnterpriseProjectFile, Report
from apps.reports.services import generate_report
from apps.rules.models import Specialty
from apps.rules.systems import TRL
from tests.test_core_flow import make_project
from tests.test_round2 import _ready_project

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_large_pdf_upload_via_http(client, enterprise, rules):
    from tests.test_core_flow import make_project

    project = make_project(enterprise, targets={TRL: 1})
    item = project.get_assessment(TRL).items.first()
    # 大于 Django 默认内存阈值（2.5MB），低于业务佐证上限
    body = b"%PDF-1.4\n" + b"x" * (3 * 1024 * 1024)
    client.force_login(enterprise)
    url = f"/evidence/items/{item.id}/upload/"
    response = client.post(
        url,
        {"file": SimpleUploadedFile("large.pdf", body, content_type="application/pdf")},
        follow=True,
    )
    assert response.status_code == 200
    item.refresh_from_db()
    assert item.active_evidence_count == 1
    evidence = EvidenceFile.objects.get(check_item=item, is_deleted=False)
    assert evidence.size_bytes == len(body)


def test_upload_pdf_uppercase_extension(enterprise, rules):
    project = EvaluationProject.objects.create(
        name="上传测试",
        applicant=enterprise.enterprise.company_name,
        tech_type="software",
        owner=enterprise,
    )
    from apps.evaluations.services import add_assessment
    from apps.rules.systems import TRL

    add_assessment(project, TRL, 1, enterprise)
    item = project.get_assessment(TRL).items.first()
    upload_evidence_file(
        item,
        enterprise,
        SimpleUploadedFile("报告.PDF", b"%PDF-1.4 test", content_type="application/pdf"),
    )
    assert item.evidence_files.filter(is_deleted=False).count() == 1


def test_project_visible_after_logout(client, enterprise, rules, mrl_rules, specialty_other):
    client.force_login(enterprise)
    response = client.post(
        "/projects/new/",
        {
            "name": "退出后仍可见",
            "domain": "测试",
            "tech_type": "hardware",
            "include_trl": "on",
            "trl_target": "2",
            "specialty": Specialty.objects.get(code="other").pk,
            "remark": "",
        },
    )
    assert response.status_code == 302
    client.logout()
    client.force_login(enterprise)
    assert "退出后仍可见" in client.get("/trl/").content.decode()


def test_batch_evidence_upload(client, enterprise, rules):
    from tests.test_core_flow import make_project

    project = make_project(enterprise, targets={})
    item = project.get_assessment("trl").items.first()
    client.force_login(enterprise)
    url = f"/evidence/items/{item.id}/upload/"
    response = client.post(
        url,
        {
            "file": [
                SimpleUploadedFile("a.pdf", b"pdf", content_type="application/pdf"),
                SimpleUploadedFile(
                    "b.docx",
                    b"docx",
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            ],
        },
        follow=True,
    )
    content = response.content.decode()
    item.refresh_from_db()
    assert item.active_evidence_count >= 2
    assert "已上传 2 份" in content or "上传成功" in content


def test_report_wbst_bound_to_version(client, enterprise, admin_user):
    from tests.report_enterprise_fixtures import minimal_docx

    project = _ready_project(enterprise, admin_user)
    assessment = project.get_assessment(TRL)
    v1 = enterprise_docs.store_upload(
        project,
        EnterpriseProjectFile.KIND_WBST,
        SimpleUploadedFile("wbst.docx", minimal_docx("wbst-archive-v1"), content_type=_DOCX),
        enterprise,
    )
    report1 = generate_report(project, Report.TYPE_TRL, admin_user)
    assert report1.wbst_file_id == v1.id
    assessment.refresh_from_db()

    v2 = enterprise_docs.store_upload(
        project,
        EnterpriseProjectFile.KIND_WBST,
        SimpleUploadedFile("wbst.docx", minimal_docx("wbst-archive-v2"), content_type=_DOCX),
        enterprise,
    )
    assert v2.id != v1.id
    eval_services.reopen_assessment(assessment, admin_user)
    report2 = generate_report(project, Report.TYPE_TRL, admin_user)
    assert report2.wbst_file_id == v2.id

    client.force_login(enterprise)
    r1 = client.get(reverse("reports:download_report_wbst", args=[report1.id]))
    r2 = client.get(reverse("reports:download_report_wbst", args=[report2.id]))
    assert r1.status_code == 200 and r2.status_code == 200
    from io import BytesIO
    from docx import Document

    assert "wbst-archive-v1" in Document(BytesIO(b"".join(r1.streaming_content))).paragraphs[0].text
    assert "wbst-archive-v2" in Document(BytesIO(b"".join(r2.streaming_content))).paragraphs[0].text

    current = client.get(reverse("reports:enterprise_download", args=[v2.id]))
    assert current.status_code == 200
    assert "wbst-archive-v2" in Document(BytesIO(b"".join(current.streaming_content))).paragraphs[0].text


def test_enterprise_template_download(client, enterprise, rules):
    from tests.test_core_flow import make_project

    project = make_project(enterprise)
    client.force_login(enterprise)
    response = client.get("/reports/templates/support/")
    assert response.status_code == 200
    assert "application" in response.get("Content-Type", "")