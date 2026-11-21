"""第二轮修改（修改意见0924）：MRL 入口开关、评价报告出具单位、两组评价组、报告导出申请、目标等级调整、管理员修改说明。"""

from io import BytesIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from docx import Document

from apps.audit.models import AuditLog
from apps.evaluations import services
from apps.evaluations.models import Assessment, CheckItem, EvaluationTeamMember, TargetChangeRequest
from apps.reports import services as report_services
from apps.reports.models import Report, ReportRequest
from apps.rules.systems import MRL, TRL
from tests.report_enterprise_fixtures import seed_enterprise_report_files
from tests.test_core_flow import fill, make_project
from tests.test_reports import _complete, _docx_text, _team

SELF, THIRD = EvaluationTeamMember.GROUP_SELF, EvaluationTeamMember.GROUP_THIRD


def _ready_project(enterprise, admin_user, target=2, groups=(SELF, THIRD), seed_enterprise=True):
    project = make_project(enterprise, targets={TRL: target})
    _complete(project.get_assessment(TRL), enterprise, admin_user)
    if groups:
        _team(project, groups)
    if seed_enterprise:
        from tests.report_enterprise_fixtures import seed_enterprise_report_files

        seed_enterprise_report_files(project, enterprise)
    return project


def _team_post(prefix, rows, report_type="trl", extra=None):
    data = {
        "type": report_type,
        "overview_text": "项目概况",
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, (role, name) in enumerate(rows):
        data.update({f"{prefix}-{index}-role": role, f"{prefix}-{index}-name": name})
    data.update(extra or {})
    return data


# ---------------------------------------------------------------------------
# 意见 1b：制造成熟度入口开关
# ---------------------------------------------------------------------------


def test_mrl_entries_hidden_until_rules_imported(client, enterprise, admin_user):
    project = make_project(enterprise, targets={TRL: 2})
    client.force_login(enterprise)
    page = client.get("/trl/").content.decode()
    assert 'href="/mrl/"' not in page and "制造成熟度评价标准及细则" not in page
    assert client.get("/mrl/").url == "/trl/"
    assert client.get(f"/projects/{project.id}/mrl/").status_code == 404
    assert client.post(f"/projects/{project.id}/mrl/add/", {"target_level": 2}).status_code == 404

    form = client.get("/projects/new/?system=mrl").content.decode()
    assert "include_mrl" not in form and "目标技术就绪度等级" in form

    detail = client.get(f"/projects/{project.id}/trl/").content.decode()
    assert f"/projects/{project.id}/mrl/" not in detail

    process = client.get("/rules/process/").content.decode()
    assert "制造成熟度（MRL）等级" not in process and "两类评价的关系" not in process

    preview = client.get(f"/reports/projects/{project.id}/?type=mrl")
    assert preview.status_code == 200 and preview.context["report_type"] == Report.TYPE_TRL
    assert [t["type"] for t in preview.context["tabs"]] == [Report.TYPE_TRL]

    # 未开放时申报只保留技术就绪度（即使不传 include_trl）
    from apps.rules.models import Specialty

    other = Specialty.objects.get_or_create(code="other", defaults={"name": "其他", "order": 0})[0]
    response = client.post(
        "/projects/new/",
        {"name": "只做TRL", "tech_type": "hardware", "trl_target": "3", "specialty": other.pk},
    )
    assert response.status_code == 302
    assert enterprise.projects.get(name="只做TRL").assessments.get().system == TRL

    client.force_login(admin_user)
    assert "制造成熟度（MRL）评价细则" not in client.get("/rules/trl/").content.decode()


def test_mrl_entries_return_after_rules_imported(client, enterprise, mrl_rules):
    project = make_project(enterprise, targets={TRL: 2})
    client.force_login(enterprise)
    assert 'href="/mrl/"' in client.get("/trl/").content.decode()
    assert client.get("/mrl/").status_code == 200
    assert "include_mrl" in client.get("/projects/new/").content.decode()
    assert client.get(f"/projects/{project.id}/mrl/").status_code == 200
    assert "制造成熟度（MRL）等级" in client.get("/rules/process/").content.decode()
    assert len(client.get(f"/reports/projects/{project.id}/").context["tabs"]) == 3


# ---------------------------------------------------------------------------
# 意见 1a：评价报告出具单位
# ---------------------------------------------------------------------------


def test_issuer_wording(client, enterprise, admin_user):
    client.force_login(enterprise)
    form = client.get("/projects/new/").content.decode()
    assert "评价报告出具单位" in form and "评价出具单位" not in form
    project = _ready_project(enterprise, admin_user, target=1)
    report = report_services.generate_report(project, Report.TYPE_TRL, admin_user)
    text = _docx_text(report)
    assert "评价报告出具单位" in text and "评价出具单位" not in text


# ---------------------------------------------------------------------------
# 意见 2：自评估工作组 / 第三方评价组
# ---------------------------------------------------------------------------


def test_enterprise_edits_only_self_team(client, enterprise, admin_user):
    project = make_project(enterprise, targets={TRL: 1})
    client.force_login(enterprise)
    preview = client.get(f"/reports/projects/{project.id}/").content.decode()
    assert "自评估工作组" in preview and 'name="team_third-TOTAL_FORMS"' not in preview

    data = _team_post("team_self", [("组长", "张工"), ("成员", "刘工")])
    data.update(_team_post("team_third", [("组长", "冒充")]))
    client.post(f"/reports/projects/{project.id}/info/", data)
    assert list(project.team_members.filter(group=SELF).values_list("name", flat=True)) == ["张工", "刘工"]
    assert not project.team_members.filter(group=THIRD).exists()

    client.force_login(admin_user)
    data = _team_post("team_third", [("组长", "杜工")])
    data.update({k: v for k, v in _team_post("team_self", []).items() if k.startswith("team_self")})
    client.post(f"/reports/projects/{project.id}/info/", data)
    assert list(project.team_members.filter(group=THIRD).values_list("name", flat=True)) == ["杜工"]
    assert project.team_members.filter(group=SELF).count() == 2


def test_report_team_table_has_two_groups(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user, target=1, seed_enterprise=False)
    report = report_services.build_report(project, Report.TYPE_TRL)
    team_block = next(b for b in report["blocks"] if b["kind"] == "team")
    assert [label for label, _ in team_block["groups"]] == ["自评估工作组", "第三方评价组"]
    assert any("成立了自评估工作组和第三方评价组" in b.get("text", "") for b in report["blocks"])

    seed_enterprise_report_files(project, enterprise)
    record = report_services.generate_report(project, Report.TYPE_TRL, admin_user)
    assert "张工" in _docx_text(record)


def test_blockers_split_by_team(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user, target=1, groups=())
    assert report_services.SELF_TEAM_MISSING in report_services.request_blockers(project, Report.TYPE_TRL)
    _team(project, (SELF,))
    blockers = report_services.request_blockers(project, Report.TYPE_TRL)
    assert report_services.SELF_TEAM_MISSING not in blockers
    assert report_services.generate_blockers(project, Report.TYPE_TRL) == [report_services.THIRD_TEAM_MISSING]
    with pytest.raises(ValidationError):
        report_services.generate_report(project, Report.TYPE_TRL, admin_user)
    seed_enterprise_report_files(project, enterprise)
    _team(project, (THIRD,))
    report_services.generate_report(project, Report.TYPE_TRL, admin_user)


# ---------------------------------------------------------------------------
# 意见 3：报告导出申请
# ---------------------------------------------------------------------------


def test_export_request_rules(enterprise, other_enterprise, admin_user):
    project = make_project(enterprise, targets={TRL: 1})
    _team(project)
    with pytest.raises(ValidationError):  # 未达到目标等级
        report_services.request_export(project, Report.TYPE_TRL, enterprise)
    _complete(project.get_assessment(TRL), enterprise, admin_user)
    seed_enterprise_report_files(project, enterprise)
    for outsider in (admin_user, other_enterprise):
        with pytest.raises(PermissionDenied):
            report_services.request_export(project, Report.TYPE_TRL, outsider)

    req = report_services.request_export(project, Report.TYPE_TRL, enterprise)
    assert req.status == ReportRequest.STATUS_PENDING
    with pytest.raises(ValidationError):  # 不能重复申请
        report_services.request_export(project, Report.TYPE_TRL, enterprise)
    with pytest.raises(PermissionDenied):
        report_services.approve_request(req, enterprise)
    with pytest.raises(ValidationError):  # 驳回须写原因
        report_services.reject_request(req, admin_user, "  ")
    report_services.reject_request(req, admin_user, "项目概况与任务书不一致")
    assert req.status == ReportRequest.STATUS_REJECTED and not project.reports.exists()

    req2 = report_services.request_export(project, Report.TYPE_TRL, enterprise)
    record = report_services.approve_request(req2, admin_user)
    req2.refresh_from_db()
    assert req2.status == ReportRequest.STATUS_APPROVED and req2.report == record and record.version == 1
    assert project.get_assessment(TRL).status == Assessment.STATUS_REPORTED
    with pytest.raises(ValidationError):
        report_services.approve_request(req2, admin_user)
    actions = set(AuditLog.objects.filter(project=project).values_list("action", flat=True))
    assert {"report.request", "report.request_reject", "report.request_approve", "report.generate"} <= actions


def test_approve_needs_third_team(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user, target=1, groups=(SELF,), seed_enterprise=False)
    seed_enterprise_report_files(project, enterprise)
    req = report_services.request_export(project, Report.TYPE_TRL, enterprise)
    with pytest.raises(ValidationError):
        report_services.approve_request(req, admin_user)
    req.refresh_from_db()
    assert req.status == ReportRequest.STATUS_PENDING
    _team(project, (THIRD,))
    report_services.approve_request(req, admin_user)


def test_direct_generate_closes_pending_request(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user, target=1)
    req = report_services.request_export(project, Report.TYPE_TRL, enterprise)
    record = report_services.generate_report(project, Report.TYPE_TRL, admin_user)
    req.refresh_from_db()
    assert req.status == ReportRequest.STATUS_APPROVED and req.report == record


def test_enterprise_report_preview_materials(client, enterprise, admin_user):
    project = _ready_project(enterprise, admin_user, target=1)
    url = f"/reports/projects/{project.id}/"
    client.force_login(enterprise)
    page = client.get(url).content.decode()
    assert "page-hero" in page and "upload-card" in page
    materials = page[page.index('class="upload-card"') :]
    assert (
        materials.index('upload-card__title">项目概况')
        < materials.index("附录A WBST")
        < materials.index('upload-card__title">评价支撑信息')
    )

    client.force_login(admin_user)
    admin_page = client.get(url).content.decode()
    assert "upload-card" in admin_page
    admin_materials = admin_page[admin_page.index('class="upload-card"') :]
    assert (
        admin_materials.index('upload-card__title">项目概况')
        < admin_materials.index("附录A WBST")
        < admin_materials.index('upload-card__title">评价支撑信息')
    )


def test_enterprise_report_preview_blockers(client, enterprise, admin_user):
    project = _ready_project(enterprise, admin_user, target=1, groups=(), seed_enterprise=False)
    client.force_login(enterprise)
    page = client.get(f"/reports/projects/{project.id}/").content.decode()
    assert "checklist--blockers" in page and "page-hero" in page


def test_export_request_pages(client, enterprise, admin_user):
    project = _ready_project(enterprise, admin_user, target=1)
    url = f"/reports/projects/{project.id}/"
    client.force_login(enterprise)
    page = client.get(url).content.decode()
    assert 'class="print-locked"' in page and "window.print()" not in page
    assert "申请报告导出…" in page and 'data-modal-open="request-modal"' in page

    client.post(f"{url}request/", {"type": "trl"})  # 未勾选确认
    assert not project.report_requests.exists()
    client.post(f"{url}request/", {"type": "trl", "confirm": "1"})
    req = project.report_requests.get()
    page = client.get(url).content.decode()
    assert "导出申请审核中" in page and 'class="print-locked"' in page

    client.force_login(admin_user)
    assert 'title="待审核的导出申请">1<' in client.get("/trl/").content.decode()
    listing = client.get("/reports/").content.decode()
    assert "待审核的导出申请" in listing and project.name in listing
    page = client.get(url).content.decode()
    assert "导出申请待审核" in page and "window.print()" in page
    client.post(f"/reports/requests/{req.id}/reject/", {"reason": ""})
    req.refresh_from_db()
    assert req.status == ReportRequest.STATUS_PENDING
    client.post(f"/reports/requests/{req.id}/reject/", {"reason": "请补充主要技术指标"})

    client.force_login(enterprise)
    page = client.get(url).content.decode()
    assert "报告导出申请未通过" in page and "请补充主要技术指标" in page
    client.post(f"{url}request/", {"type": "trl", "confirm": "1"})
    req2 = project.report_requests.get(status=ReportRequest.STATUS_PENDING)
    assert client.post(f"/reports/requests/{req2.id}/approve/").status_code == 302
    req2.refresh_from_db()
    assert req2.status == ReportRequest.STATUS_PENDING  # 企业不能自己审核

    client.force_login(admin_user)
    client.post(f"/reports/requests/{req2.id}/approve/")
    report = project.reports.get()
    client.force_login(enterprise)
    page = client.get(url).content.decode()
    assert 'class="print-locked"' not in page and "window.print()" in page and "报告导出申请已通过" in page
    assert client.get(f"/reports/{report.id}/download/").status_code == 200


# ---------------------------------------------------------------------------
# 意见 4：目标等级调整
# ---------------------------------------------------------------------------


def test_target_change_rules(enterprise, other_enterprise, admin_user):
    assessment = make_project(enterprise, targets={TRL: 3}).get_assessment(TRL)
    fill(assessment, 1, enterprise)
    fill(assessment, 2, enterprise)
    services.submit_levels(assessment, enterprise, 2)

    for bad_level in (3, 1, 10):  # 与当前相同 / 低于已提交 / 超出范围
        with pytest.raises(ValidationError):
            services.request_target_change(assessment, enterprise, bad_level, "理由")
    with pytest.raises(ValidationError):
        services.request_target_change(assessment, enterprise, 4, " ")
    for outsider in (admin_user, other_enterprise):
        with pytest.raises(PermissionDenied):
            services.request_target_change(assessment, outsider, 4, "理由")
    options = dict((level, ok) for level, ok, _ in services.target_options(assessment))
    assert options[1] is False and options[2] is True and options[3] is False and options[9] is True

    req = services.request_target_change(assessment, enterprise, 2, "先完成 2 级")
    with pytest.raises(ValidationError):
        services.request_target_change(assessment, enterprise, 4, "理由")
    with pytest.raises(PermissionDenied):
        services.approve_target_change(req, enterprise)
    with pytest.raises(ValidationError):
        services.reject_target_change(req, admin_user, "")
    services.reject_target_change(req, admin_user, "3 级材料已基本齐全，不建议调低")
    assessment.refresh_from_db()
    assert assessment.target_level == 3

    req = services.request_target_change(assessment, enterprise, 5, "样机已完成环境试验")
    services.approve_target_change(req, admin_user)
    assessment.refresh_from_db()
    assert assessment.target_level == 5 and req.status == TargetChangeRequest.STATUS_APPROVED
    assert services.is_level_editable(assessment, 5)
    actions = set(AuditLog.objects.filter(action__startswith="target.").values_list("action", flat=True))
    assert actions == {"target.request", "target.approve", "target.reject"}


def test_raising_target_after_report_reopens_assessment(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user, target=1)
    report_services.generate_report(project, Report.TYPE_TRL, admin_user)
    assessment = project.get_assessment(TRL)
    req = services.request_target_change(assessment, enterprise, 2, "申请更高一级")
    services.approve_target_change(req, admin_user)
    assessment.refresh_from_db()
    assert assessment.status == Assessment.STATUS_IN_PROGRESS and assessment.target_level == 2
    assert project.reports.count() == 1


def test_assessment_detail_kpi_before_alerts(client, enterprise):
    project = make_project(enterprise, targets={TRL: 2})
    client.force_login(enterprise)
    page = client.get(f"/projects/{project.id}/trl/").content.decode()
    assert page.index('class="kpi-row') < page.index('class="alert-stack')


def test_target_change_pages(client, enterprise, admin_user):
    project = make_project(enterprise, targets={TRL: 2})
    detail = f"/projects/{project.id}/trl/"
    client.force_login(enterprise)
    assert "申请调整" in client.get("/trl/").content.decode()
    page = client.get(f"{detail}?adjust=1").content.decode()
    assert 'data-auto-modal="target-modal"' in page and 'name="to_level"' in page
    client.post(f"{detail}target/", {"to_level": "3", "reason": "申请调高"})
    req = TargetChangeRequest.objects.get()
    assert "调整审核中 → TRL 3" in client.get("/trl/").content.decode()
    detail_page = client.get(detail).content.decode()
    assert "调整审核中 → TRL 3" in detail_page and 'class="kpi-row' in detail_page

    client.force_login(admin_user)
    assert 'title="待审核">1<' in client.get("/trl/").content.decode()
    page = client.get(detail).content.decode()
    assert "目标等级调整申请：TRL 2 → TRL 3" in page and "申请调高" in page
    client.post(f"/target-requests/{req.id}/approve/")
    assert project.get_assessment(TRL).target_level == 3


# ---------------------------------------------------------------------------
# 意见 5：管理员修改满足情况说明 / 评价支撑信息
# ---------------------------------------------------------------------------


def test_admin_edit_item_text(client, enterprise, admin_user):
    project = make_project(enterprise, targets={TRL: 2})
    assessment = project.get_assessment(TRL)
    fill(assessment, 1, enterprise)
    item = assessment.items.filter(level=1).first()
    with pytest.raises(ValidationError):  # 未提交的级别由企业修改
        services.admin_edit_item_text(item, admin_user, "改", "")
    services.submit_levels(assessment, enterprise, 1)
    with pytest.raises(PermissionDenied):
        services.admin_edit_item_text(item, enterprise, "改", "")
    with pytest.raises(ValidationError):
        services.admin_edit_item_text(item, admin_user, "", "")

    client.force_login(admin_user)
    page = client.get(f"/projects/{project.id}/trl/?level=1").content.decode()
    assert f"/items/{item.id}/admin-edit/" in page
    client.post(f"/items/{item.id}/admin-edit/", {"statement": "评价组核实后的说明", "support_info": "《测试报告》"})
    item.refresh_from_db()
    assert item.statement == "评价组核实后的说明" and item.support_info == "《测试报告》"
    assert item.result == "satisfied"
    log = AuditLog.objects.get(action="item.admin_edit")
    assert log.before["statement"] == "已核实" and log.after["statement"] == "评价组核实后的说明"

    client.force_login(enterprise)
    page = client.get(f"/projects/{project.id}/trl/?level=1").content.decode()
    assert "评价机构修改了满足情况说明和评价支撑信息" in page and "评价组核实后的说明" in page
    assert f"/items/{item.id}/admin-edit/" not in page

    services.pass_levels(assessment, admin_user, 1)
    services.admin_edit_item_text(item, admin_user, "通过后再次修正", "")  # 已通过的级别仍可修改
    fill(assessment, 2, enterprise)
    services.submit_levels(assessment, enterprise, 2)
    services.pass_levels(assessment, admin_user, 2)
    _team(project)
    seed_enterprise_report_files(project, enterprise)
    report_services.generate_report(project, Report.TYPE_TRL, admin_user)
    item = CheckItem.objects.get(pk=item.pk)  # 重新读取，避免沿用缓存的评价状态
    with pytest.raises(ValidationError):  # 已出报告需先重新开放
        services.admin_edit_item_text(item, admin_user, "再改", "")
