"""第三轮：项目删除（企业未提交前可删，管理员都可删）、回收站、彻底删除与审计保留。"""

from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.evaluations import services
from apps.evaluations.models import CheckItem, EvaluationProject
from apps.evidence.services import upload_evidence_file
from apps.notifications.models import Notification
from apps.reports.services import generate_report
from apps.reports.models import Report
from apps.rules.systems import TRL
from tests.test_core_flow import fill, make_project
from tests.test_round2 import _ready_project


def _pdf():
    return SimpleUploadedFile("a.pdf", b"%PDF-1.4 test", content_type="application/pdf")


def test_enterprise_can_delete_only_before_submission(enterprise, other_enterprise, admin_user):
    project = make_project(enterprise, targets={TRL: 1})
    assessment = project.get_assessment(TRL)
    assert services.can_delete_project(enterprise, project)
    assert not services.can_delete_project(other_enterprise, project)
    with pytest.raises(PermissionDenied):
        services.delete_project(project, other_enterprise)

    fill(assessment, 1, enterprise)
    services.submit_levels(assessment, enterprise, 1)
    assert not services.can_delete_project(enterprise, project)
    with pytest.raises(ValidationError):
        services.delete_project(project, enterprise)
    # 退回后也不能删：提交过就留有审核记录
    services.return_levels(assessment, admin_user, [1], "补充材料")
    assert not services.can_delete_project(enterprise, project)

    fresh = make_project(enterprise, targets={TRL: 1})
    services.delete_project(fresh, enterprise)
    fresh.refresh_from_db()
    assert fresh.is_deleted and fresh.deleted_by == enterprise
    # 企业自删不通知自己，也不打扰管理员
    assert not Notification.objects.filter(kind="project.delete").exists()


def test_admin_delete_requires_reason_and_notifies_owner(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user)
    assert services.can_delete_project(admin_user, project)
    with pytest.raises(ValidationError):
        services.delete_project(project, admin_user, "  ")
    services.delete_project(project, admin_user, "重复申报")
    note = Notification.objects.get(recipient=enterprise, kind="project.delete")
    assert "重复申报" in note.body
    with pytest.raises(ValidationError):
        services.delete_project(project, admin_user, "再删一次")


def test_deleted_project_is_hidden_everywhere(client, enterprise, admin_user):
    project = _ready_project(enterprise, admin_user)
    item = project.get_assessment(TRL).items.first()
    report = generate_report(project, Report.TYPE_TRL, admin_user)
    draft = make_project(enterprise, targets={TRL: 1})
    evidence = upload_evidence_file(draft.get_assessment(TRL).items.first(), enterprise, _pdf())
    services.delete_project(project, admin_user, "测试删除")
    services.delete_project(draft, enterprise)

    assert not EvaluationProject.objects.filter(pk=project.pk).exists()
    assert EvaluationProject.all_objects.filter(pk=project.pk).exists()
    for user in (enterprise, admin_user):
        client.force_login(user)
        assert project.name not in client.get("/trl/").content.decode()
        for url in [
            f"/projects/{project.id}/",
            f"/projects/{project.id}/trl/",
            f"/reports/projects/{project.id}/",
            f"/reports/{report.id}/download/",
        ]:
            assert client.get(url).status_code == 404, (user.username, url)
        assert client.post(f"/items/{item.id}/evaluate/", {"result": "satisfied", "statement": "x"}).status_code == 404
        assert project.name not in client.get("/reports/").content.decode()
        assert client.get(f"/evidence/{evidence.id}/download/").status_code == 404


def test_badges_skip_deleted_projects(client, enterprise, admin_user):
    project = make_project(enterprise, targets={TRL: 1})
    assessment = project.get_assessment(TRL)
    fill(assessment, 1, enterprise)
    services.submit_levels(assessment, enterprise, 1)
    client.force_login(admin_user)
    assert 'title="待审核">1<' in client.get("/trl/").content.decode()
    services.delete_project(project, admin_user, "测试")
    assert 'title="待审核">' not in client.get("/trl/").content.decode()


def test_delete_views_and_recycle_bin(client, enterprise, admin_user):
    project = make_project(enterprise, targets={TRL: 1})
    client.force_login(enterprise)
    page = client.get(f"/projects/{project.id}/trl/").content.decode()
    assert "删除项目" in page
    # 未勾选确认不删除
    client.post(f"/projects/{project.id}/delete/", {})
    project.refresh_from_db()
    assert not project.is_deleted
    response = client.post(f"/projects/{project.id}/delete/", {"confirm": "1"})
    assert response.status_code == 302
    project.refresh_from_db()
    assert project.is_deleted
    assert client.get("/recycle-bin/").status_code == 403

    client.force_login(admin_user)
    page = client.get("/recycle-bin/").content.decode()
    assert project.name in page and "企业删除" in page
    client.post(f"/recycle-bin/{project.id}/restore/")
    project.refresh_from_db()
    assert not project.is_deleted
    assert Notification.objects.filter(recipient=enterprise, kind="project.restore").exists()
    # 不在回收站中的项目不能彻底删除
    assert client.post(f"/recycle-bin/{project.id}/purge/", {"confirm": "1"}).status_code == 404


def test_purge_removes_records_and_files_but_keeps_audit(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user)
    item = project.get_assessment(TRL).items.first()
    report = generate_report(project, Report.TYPE_TRL, admin_user)
    report_dir = Path(settings.MEDIA_ROOT) / "reports" / str(project.id)
    assert report_dir.exists()
    with pytest.raises(ValidationError):
        services.purge_project(project, admin_user)
    services.delete_project(project, admin_user, "测试")
    with pytest.raises(PermissionDenied):
        services.purge_project(project, enterprise)

    # 测试在事务里运行，显式执行 on_commit 回调以验证文件清理
    with TestCase.captureOnCommitCallbacks(execute=True):
        services.purge_project(project, admin_user)
    assert not EvaluationProject.all_objects.filter(pk=project.pk).exists()
    assert not CheckItem.objects.filter(pk=item.pk).exists()
    assert not Report.objects.filter(pk=report.pk).exists()
    assert not report_dir.exists()
    logs = AuditLog.objects.filter(project_name=project.name)
    assert logs.filter(action="project.purge").exists()
    assert logs.filter(action="project.create").exists()
    assert not logs.exclude(project=None).exists()


def test_audit_page_shows_deleted_project_name(client, enterprise, admin_user):
    project = make_project(enterprise, targets={TRL: 1})
    services.delete_project(project, admin_user, "测试")
    client.force_login(admin_user)
    page = client.get("/audit/").content.decode()
    assert "在回收站中" in page and "删除项目" in page


def test_new_pages_have_no_forbidden_wording(client, enterprise, admin_user):
    from tests.test_accounts_access import FORBIDDEN_WORDS

    project = make_project(enterprise, targets={TRL: 1})
    services.delete_project(make_project(enterprise, targets={TRL: 1}), admin_user, "测试")
    client.force_login(admin_user)
    for url in ("/recycle-bin/", "/settings/", f"/projects/{project.id}/trl/"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert not FORBIDDEN_WORDS.search(response.content.decode()), url
