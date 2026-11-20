import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.evaluations.models import EvaluationProject
from apps.evidence.services import upload_evidence_file
from apps.rules.models import Specialty


def test_large_pdf_upload_allowed_by_settings(settings):
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 52 * 1024 * 1024


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


def test_enterprise_template_download(client, enterprise, rules):
    from tests.test_core_flow import make_project

    project = make_project(enterprise)
    client.force_login(enterprise)
    response = client.get("/reports/templates/support/")
    assert response.status_code == 200
    assert "application" in response.get("Content-Type", "")