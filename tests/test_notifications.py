"""第三轮：站内私信（收件箱、未读数、原有流程节点的通知矩阵）。"""

from django.contrib.auth.models import User

from apps.accounts import services as account_services
from apps.accounts.models import EnterpriseProfile
from apps.evaluations import services
from apps.notifications.models import Notification
from apps.notifications.services import notify, remind_once
from apps.reports import services as report_services
from apps.reports.models import Report
from apps.rules.systems import TRL
from tests.conftest import make_enterprise
from tests.test_core_flow import fill, make_project
from tests.test_round2 import _ready_project


def kinds(user):
    return list(Notification.objects.filter(recipient=user).values_list("kind", flat=True))


def test_inbox_isolation_and_unread_badge(client, enterprise, other_enterprise, admin_user):
    notify(enterprise, "x.test", "给甲企业的消息", link="/trl/")
    notify(other_enterprise, "x.test", "给乙企业的消息")
    client.force_login(enterprise)
    page = client.get("/inbox/").content.decode()
    assert "给甲企业的消息" in page and "给乙企业的消息" not in page
    # 页眉铃铛显示未读数
    assert 'class="nav-count">1<' in client.get("/trl/").content.decode()

    mine = Notification.objects.get(recipient=enterprise)
    theirs = Notification.objects.get(recipient=other_enterprise)
    assert client.post(f"/inbox/{theirs.pk}/open/").status_code == 404
    response = client.post(f"/inbox/{mine.pk}/open/")
    assert response.status_code == 302 and response.url == "/trl/"
    mine.refresh_from_db()
    assert mine.read_at is not None
    assert 'class="nav-count">' not in client.get("/trl/").content.decode()


def test_open_rejects_external_link(client, enterprise):
    notify(enterprise, "x.test", "外部链接", link="https://evil.example.com/")
    notify(enterprise, "x.test", "协议相对链接", link="//evil.example.com/")
    client.force_login(enterprise)
    for n in Notification.objects.filter(recipient=enterprise):
        assert client.post(f"/inbox/{n.pk}/open/").url == "/inbox/"


def test_read_all_and_unread_filter(client, enterprise):
    for i in range(3):
        notify(enterprise, "x.test", f"消息{i}")
    client.force_login(enterprise)
    assert client.get("/inbox/?show=unread").content.decode().count("inbox-row unread") == 3
    client.post("/inbox/read-all/")
    assert not Notification.objects.filter(recipient=enterprise, read_at__isnull=True).exists()
    assert "没有未读消息" in client.get("/inbox/?show=unread").content.decode()


def test_remind_once_is_idempotent(db):
    assert remind_once("assign_overdue:1:20261016", "assign.overdue") is True
    assert remind_once("assign_overdue:1:20261016", "assign.overdue") is False
    assert remind_once("assign_overdue:1:20261023", "assign.overdue") is True


def test_account_nodes(rules, admin_user):
    user = make_enterprise("gamma", "丙企业有限公司", 3, status=EnterpriseProfile.STATUS_PENDING)
    profile = user.enterprise
    # 注册走服务函数时管理员收到待办
    account_services.register_enterprise(
        {
            "username": "delta01",
            "password1": "Abcd1234",
            "contact_name": "李四",
            "company_name": "丁企业有限公司",
            "credit_code": "91110000MA00000088",
            "phone": "13900000000",
        }
    )
    assert kinds(admin_user) == ["user.register"]
    assert Notification.objects.get(recipient=admin_user).category == "todo"
    account_services.change_status(profile, admin_user, "approve")
    assert kinds(user) == ["user.approve"]
    # 驳回、停用不发给企业（企业此时无法登录）
    other = User.objects.get(username="delta01").enterprise
    account_services.change_status(other, admin_user, "reject", "信息不全")
    assert kinds(other.user) == []


def test_direct_review_nodes(enterprise, admin_user):
    project = make_project(enterprise, targets={TRL: 2})
    assessment = project.get_assessment(TRL)
    fill(assessment, 1, enterprise)
    fill(assessment, 2, enterprise)
    services.submit_levels(assessment, enterprise, 2)
    assert kinds(admin_user) == ["level.submit"]
    services.pass_levels(assessment, admin_user, 1)
    services.return_levels(assessment, admin_user, [2], "请补充佐证")
    assert sorted(kinds(enterprise)) == ["level.pass", "level.return"]
    returned = Notification.objects.get(recipient=enterprise, kind="level.return")
    assert "请补充佐证" in returned.body and returned.category == "todo"
    assert returned.link.startswith(f"/projects/{project.id}/trl/")

    item = assessment.items.filter(level=2).first()
    services.admin_edit_item_text(item, admin_user, "管理员修改后的说明", "")
    assert Notification.objects.filter(recipient=enterprise, kind="item.admin_edit").exists()


def test_target_change_nodes(enterprise, admin_user):
    project = make_project(enterprise, targets={TRL: 2})
    assessment = project.get_assessment(TRL)
    request = services.request_target_change(assessment, enterprise, 3, "进展较快")
    assert kinds(admin_user) == ["target.request"]
    services.reject_target_change(request, admin_user, "依据不足")
    request = services.request_target_change(assessment, enterprise, 4, "补充了依据")
    services.approve_target_change(request, admin_user)
    assert sorted(kinds(enterprise)) == ["target.approve", "target.reject"]


def test_report_nodes(enterprise, admin_user):
    project = _ready_project(enterprise, admin_user)
    Notification.objects.all().delete()
    request = report_services.request_export(project, Report.TYPE_TRL, enterprise)
    assert kinds(admin_user) == ["report.request"]
    report_services.reject_request(request, admin_user, "请核对评价组")
    request = report_services.request_export(project, Report.TYPE_TRL, enterprise)
    report_services.approve_request(request, admin_user)
    assert sorted(kinds(enterprise)) == ["report.generate", "report.request_reject"]
    generated = Notification.objects.get(recipient=enterprise, kind="report.generate")
    # 报告出具是邮件预留节点
    assert generated.email_pending is True
    assert not Notification.objects.filter(email_pending=True).exclude(kind="report.generate").exists()


def test_inbox_page_has_no_forbidden_wording(client, enterprise, admin_user):
    from tests.test_accounts_access import FORBIDDEN_WORDS

    project = make_project(enterprise, targets={TRL: 1})
    fill(project.get_assessment(TRL), 1, enterprise)
    services.submit_levels(project.get_assessment(TRL), enterprise, 1)
    for user in (enterprise, admin_user):
        client.force_login(user)
        for url in ("/inbox/", "/inbox/?show=unread"):
            content = client.get(url).content.decode()
            assert not FORBIDDEN_WORDS.search(content), (user.username, url)
