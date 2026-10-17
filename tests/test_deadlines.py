"""第三轮：期限检查（专家逾期、整改即将到期、整改逾期）与专家劳务记录。"""

from datetime import timedelta
from io import BytesIO

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from openpyxl import load_workbook

from apps.notifications.models import Notification
from apps.reviews import fees, services
from apps.reviews.deadlines import check_deadlines
from apps.reviews.middleware import CACHE_KEY
from apps.reviews.models import ExpertAssignment, RemunerationRecord, ReviewRound
from tests.test_expert_review import experts, expert_project, opine, start_review  # noqa: F401


def _overdue(assignment, days=2):
    ExpertAssignment.objects.filter(pk=assignment.pk).update(due_at=timezone.now() - timedelta(days=days))
    assignment.refresh_from_db()


def test_expert_overdue_is_computed_and_reminded_once(enterprise, admin_user, experts):
    project, assessment, review_round = start_review(enterprise, admin_user, experts)
    a1 = review_round.assignments.get(slot=1)
    assert not a1.is_overdue
    assert check_deadlines()["assign_overdue"] == 0
    _overdue(a1, days=3)
    assert a1.is_overdue and a1.overdue_days == 3
    assert check_deadlines()["assign_overdue"] == 1
    assert check_deadlines()["assign_overdue"] == 0
    note = Notification.objects.get(recipient=admin_user, kind="review.overdue")
    assert note.category == "alert" and "3 天" in note.title
    # 企业和专家都不收到逾期预警
    assert not Notification.objects.filter(kind="review.overdue").exclude(recipient=admin_user).exists()
    # 延期后截止时间变化，再次逾期会重新提醒
    services.extend_assignment(a1, admin_user, 1)
    a1.refresh_from_db()
    assert not a1.is_overdue
    _overdue(a1, days=1)
    assert check_deadlines()["assign_overdue"] == 1
    # 已提交的专家不算逾期
    opine(a1)
    a1.refresh_from_db()
    assert not a1.is_overdue
    assert check_deadlines()["assign_overdue"] == 0


def test_remind_and_extend(enterprise, admin_user, experts):
    project, assessment, review_round = start_review(enterprise, admin_user, experts)
    a2 = review_round.assignments.get(slot=2)
    _overdue(a2)
    services.remind_expert(a2, admin_user)
    a2.refresh_from_db()
    assert a2.remind_count == 1
    note = Notification.objects.get(recipient=experts[1], kind="review.remind")
    assert note.email_pending and "已逾期" in note.title
    services.extend_assignment(a2, admin_user, 5)
    a2.refresh_from_db()
    assert 4 <= (a2.due_at - timezone.now()).days <= 5
    with pytest.raises(ValidationError):
        services.extend_assignment(a2, admin_user, 0)


def _rectifying(enterprise, admin_user, experts):
    project, assessment, review_round = start_review(enterprise, admin_user, experts)
    code = services.task_items(review_round.assignments.first())[0].check_item.code
    for assignment in review_round.assignments.all():
        opine(assignment, fail_codes=[code])
    review_round.refresh_from_db()
    services.confirm_unanimous(review_round, admin_user)
    for ri in review_round.items.filter(final_result="fail"):
        services.rule_item(ri, admin_user, "fail", feedback="补充")
    services.finalize_round(review_round, admin_user, rectify_days=14)
    review_round.refresh_from_db()
    return project, review_round


def test_rectify_soon_and_overdue(enterprise, admin_user, experts):
    project, review_round = _rectifying(enterprise, admin_user, experts)
    assert check_deadlines() == {"assign_overdue": 0, "rectify_soon": 0, "rectify_overdue": 0}
    ReviewRound.objects.filter(pk=review_round.pk).update(rectify_due_at=timezone.now() + timedelta(days=1))
    assert check_deadlines()["rectify_soon"] == 1
    assert check_deadlines()["rectify_soon"] == 0
    assert Notification.objects.filter(recipient=enterprise, kind="review.rectify_soon").exists()
    ReviewRound.objects.filter(pk=review_round.pk).update(rectify_due_at=timezone.now() - timedelta(hours=1))
    review_round.refresh_from_db()
    assert review_round.rectify_overdue
    assert check_deadlines()["rectify_overdue"] == 1
    assert Notification.objects.filter(recipient=enterprise, kind="review.rectify_overdue").exists()
    assert Notification.objects.filter(recipient=admin_user, kind="review.rectify_overdue").exists()
    services.extend_rectification(review_round, admin_user, 7)
    review_round.refresh_from_db()
    assert not review_round.rectify_overdue
    assert Notification.objects.filter(recipient=enterprise, kind="review.rectify_extend").exists()


def test_middleware_runs_check_throttled(client, settings, enterprise, admin_user, experts):
    project, assessment, review_round = start_review(enterprise, admin_user, experts)
    _overdue(review_round.assignments.get(slot=3))
    settings.DEADLINE_CHECK_ENABLED = True
    cache.delete(CACHE_KEY)
    client.force_login(enterprise)
    client.get("/inbox/")
    assert Notification.objects.filter(kind="review.overdue").count() == 1
    _overdue(review_round.assignments.get(slot=2))
    client.get("/inbox/")  # 10 分钟内不再检查
    assert Notification.objects.filter(kind="review.overdue").count() == 1
    cache.delete(CACHE_KEY)
    client.get("/inbox/")
    assert Notification.objects.filter(kind="review.overdue").count() == 2
    # 未登录访问不触发
    cache.delete(CACHE_KEY)
    client.logout()
    client.get("/login/")
    assert cache.get(CACHE_KEY) is None


def test_fee_update_settle_and_export(client, enterprise, admin_user, experts):
    project, assessment, review_round = start_review(enterprise, admin_user, experts)
    for assignment in review_round.assignments.all():
        opine(assignment)
    records = list(RemunerationRecord.objects.order_by("id"))
    assert len(records) == 3 and all(r.amount == 0 for r in records)
    with pytest.raises(ValidationError):
        fees.update_record(records[0], admin_user, -1)
    fees.update_record(records[0], admin_user, "850.5", "含交通补贴")
    records[0].refresh_from_db()
    assert str(records[0].amount) == "850.50"
    assert fees.settle_records([records[0].id, records[1].id], admin_user) == 2
    with pytest.raises(ValidationError):
        fees.settle_records([records[0].id], admin_user)
    records[0].refresh_from_db()
    with pytest.raises(ValidationError):
        fees.update_record(records[0], admin_user, 900)
    assert Notification.objects.filter(recipient=experts[0], kind="fee.settle").exists()

    client.force_login(enterprise)
    assert client.get("/experts/fees/").status_code == 403
    client.force_login(admin_user)
    page = client.get("/experts/fees/?status=unsettled").content.decode()
    assert page.count('name="ids"') == 1
    csv_bytes = client.get("/experts/fees/export.csv").content
    assert csv_bytes.startswith("﻿".encode("utf-8")) and "已结算".encode("utf-8") in csv_bytes
    workbook = load_workbook(BytesIO(client.get("/experts/fees/export.xlsx?status=settled").content))
    rows = list(workbook.active.values)
    assert rows[0][0] == "专家" and len(rows) == 3
    # 专家在自己的档案页看到劳务记录
    client.force_login(experts[0])
    assert "劳务记录" in client.get(f"/experts/{experts[0].expert_profile.pk}/").content.decode()
    # 项目彻底删除后劳务记录保留项目名称
    from apps.evaluations import services as eval_services
    from django.test import TestCase

    eval_services.delete_project(project, admin_user, "测试")
    with TestCase.captureOnCommitCallbacks(execute=True):
        eval_services.purge_project(project, admin_user)
    assert RemunerationRecord.objects.filter(project__isnull=True, project_name=project.name).count() == 3
