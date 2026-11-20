"""第三轮：专家评审全流程与单盲。

企业照常逐级提交审核；评价机构对已提交的级别决定自行审核，或指派 3 位专家评审后终审。
终审后全部通过的级别审核通过，有不通过条目的级别退回企业整改；整改后再次提交，由评价机构决定复评方式。
"""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import SystemConfig
from apps.audit.models import AuditLog
from apps.evaluations import services as eval_services
from apps.evaluations.models import CheckItem, EvaluationProject, EvaluationTeamMember, LevelReview
from apps.evidence.services import upload_evidence_file
from apps.notifications.models import Notification
from apps.reports.models import Report
from apps.reports.services import generate_report
from apps.reviews import services
from apps.reviews.models import ExpertAssignment, RemunerationRecord, ReviewRound, RoundItem
from apps.rules.systems import TRL
from tests.test_core_flow import make_project
from tests.test_experts import make_expert

MARK = "专家原话标记"


@pytest.fixture
def experts(admin_user):
    return [make_expert(admin_user, name, "software", username=f"rev{i}") for i, name in enumerate(["赵评审", "钱评审", "孙评审", "李评审"])]


def _pdf(name="a.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def expert_project(enterprise, evidence=True, submit=True):
    """软件类项目，目标 TRL 3（第 1~3 级共 10 条），填完后照常提交审核。"""
    project = make_project(enterprise, tech_type=EvaluationProject.TECH_SOFTWARE, targets={TRL: 3})
    assessment = project.get_assessment(TRL)
    if evidence:
        upload_evidence_file(assessment.items.order_by("order_index").first(), enterprise, _pdf())
    assessment.items.filter(level__lte=3).update(result=CheckItem.RESULT_SATISFIED, statement="已核实")
    if submit:
        eval_services.submit_levels(assessment, enterprise, 3)
    assessment.refresh_from_db()
    return project, assessment


def start_review(enterprise, admin_user, experts):
    project, assessment = expert_project(enterprise)
    review_round = services.start_expert_review(assessment, admin_user)
    services.assign_experts(review_round, admin_user, [e.pk for e in experts[:3]])
    for assignment in review_round.assignments.all():
        services.respond_assignment(assignment, assignment.expert, True, pledge=True)
    review_round.refresh_from_db()
    return project, assessment, review_round


def opine(assignment, fail_codes=(), extra=""):
    data = {}
    for ri in services.task_items(assignment):
        fail = ri.check_item.code in fail_codes
        data[ri.id] = {
            "verdict": "fail" if fail else "pass",
            "suggestion": f"{MARK}{extra}建议补充" if fail else "",
            "comment": f"{MARK}{extra}支撑材料评价",
        }
    services.save_opinions(assignment, assignment.expert, data, submit=True)


def _levels(assessment):
    return {r.level: (r.status, r.source) for r in LevelReview.objects.filter(assessment=assessment)}


def test_enterprise_cannot_choose_review_way(client, enterprise, admin_user):
    project, assessment = expert_project(enterprise, submit=False)
    # 企业只能填到目标等级，评审方式不归企业选择
    level9 = assessment.items.filter(level=9).first()
    with pytest.raises(ValidationError, match="超出目标等级"):
        eval_services.evaluate_check_item(level9, enterprise, {"result": "satisfied", "statement": "x"})
    with pytest.raises(PermissionDenied):
        services.start_expert_review(assessment, enterprise)
    client.force_login(enterprise)
    page = client.get(f"/projects/{project.id}/trl/").content.decode()
    assert "提交审核" in page
    assert not any(word in page for word in ["指派专家", "改为专家评审", "申报专家评审", "评审方式"])
    response = client.post(f"/reviews/assessments/{assessment.id}/start/", {"reuse": "0"})
    assert response.status_code in (403, 404)
    assert not assessment.review_rounds.exists()


def test_admin_decides_after_submission(client, enterprise, admin_user):
    project, assessment = expert_project(enterprise, submit=False)
    with pytest.raises(ValidationError, match="尚未提交"):
        services.start_expert_review(assessment, admin_user)
    eval_services.submit_levels(assessment, enterprise, 3)
    assert Notification.objects.filter(recipient=admin_user, kind="level.submit").exists()

    # 管理员在待审核面板看到两种处理方式
    client.force_login(admin_user)
    page = client.get(f"/projects/{project.id}/trl/").content.decode()
    assert "审核通过…" in page and "指派专家评审…" in page and "expert-start-modal" in page
    response = client.post(f"/reviews/assessments/{assessment.id}/start/", {"reuse": "0", "review_days": "10"})
    review_round = assessment.review_rounds.get()
    assert response.status_code == 302 and response["Location"].endswith(f"/reviews/rounds/{review_round.id}/assign/")
    assert review_round.number == 1 and review_round.status == ReviewRound.STATUS_ASSIGNING
    assert review_round.review_days == 10
    assert review_round.items.count() == assessment.items.filter(level__lte=3).count() == 10
    assert AuditLog.objects.filter(action="review.start", actor=admin_user).exists()

    # 评审期间：管理员不能直接通过或退回，也不能重复指派；等级显示“专家评审中”，企业不能删除项目
    with pytest.raises(ValidationError, match="专家评审正在进行"):
        eval_services.pass_levels(assessment, admin_user, 3)
    with pytest.raises(ValidationError, match="专家评审正在进行"):
        eval_services.return_levels(assessment, admin_user, [1], "退回")
    with pytest.raises(ValidationError, match="正在进行"):
        services.start_expert_review(assessment, admin_user)
    states = {s["level"]: s["state"] for s in eval_services.get_level_states(assessment)}
    assert states[1] == states[3] == "expert_review"
    assert not eval_services.can_delete_project(enterprise, project)
    page = client.get(f"/projects/{project.id}/trl/").content.decode()
    assert "正在进行第 1 轮专家评审" in page and "审核通过…" not in page

    # 企业：只看到评价机构在安排专家评审，看不到评审方式选择
    client.force_login(enterprise)
    assert "评价机构正在安排专家评审" in client.get("/trl/").content.decode()


def test_admin_self_review_unchanged(enterprise, admin_user):
    """管理员选择自行审核时与原流程一致，不产生专家评审轮次。"""
    project, assessment = expert_project(enterprise)
    eval_services.pass_levels(assessment, admin_user, 3)
    assert _levels(assessment) == {lv: ("passed", "direct") for lv in (1, 2, 3)}
    assert eval_services.ready_for_report(assessment)
    assert not assessment.review_rounds.exists()
    assert services.start_blockers(assessment) == ["企业尚未提交待审核的级别"]


def test_assign_exactly_three(enterprise, admin_user, experts):
    project, assessment = expert_project(enterprise)
    review_round = services.start_expert_review(assessment, admin_user)
    with pytest.raises(ValidationError, match="3 位"):
        services.assign_experts(review_round, admin_user, [experts[0].pk, experts[1].pk])
    with pytest.raises(PermissionDenied):
        services.assign_experts(review_round, enterprise, [e.pk for e in experts[:3]])
    services.assign_experts(review_round, admin_user, [e.pk for e in experts[:3]], review_days=10)
    review_round.refresh_from_db()
    assert review_round.status == ReviewRound.STATUS_REVIEWING and review_round.review_days == 10
    assert sorted(review_round.assignments.values_list("slot", flat=True)) == [1, 2, 3]
    # 专家收到任务（邮件节点），企业只收到“已安排专家评审”，不含专家姓名
    task = Notification.objects.get(recipient=experts[0], kind="review.assign")
    assert task.email_pending and task.category == "task"
    formed = Notification.objects.get(recipient=enterprise, kind="review.group_formed")
    assert "第 1~3 级" in formed.title
    assert not any(name in formed.title + formed.body for name in ["赵评审", "钱评审", "孙评审"])
    with pytest.raises(ValidationError):
        services.assign_experts(review_round, admin_user, [experts[3].pk])
    with pytest.raises(IntegrityError), transaction.atomic():
        ExpertAssignment.objects.create(round=review_round, expert=experts[3], slot=1, due_at=timezone.now())


def test_pledge_decline_and_reassign(enterprise, admin_user, experts):
    project, assessment = expert_project(enterprise)
    review_round = services.start_expert_review(assessment, admin_user)
    services.assign_experts(review_round, admin_user, [e.pk for e in experts[:3]])
    a1, a2, a3 = review_round.assignments.order_by("slot")
    with pytest.raises(ValidationError, match="承诺"):
        services.respond_assignment(a1, experts[0], True, pledge=False)
    with pytest.raises(PermissionDenied):
        services.respond_assignment(a1, experts[1], True, pledge=True)
    services.respond_assignment(a1, experts[0], True, pledge=True)
    a1.refresh_from_db()
    assert a1.status == "accepted" and a1.pledge_version == services.PLEDGE_VERSION and a1.pledged_at

    with pytest.raises(ValidationError, match="原因"):
        services.respond_assignment(a2, experts[1], False)
    services.respond_assignment(a2, experts[1], False, reason="与企业有合作，需回避")
    review_round.refresh_from_db()
    assert review_round.status == ReviewRound.STATUS_ASSIGNING
    assert Notification.objects.filter(recipient=admin_user, kind="review.decline", category="todo").exists()
    # 拒绝过的专家不再出现在候选中
    assert experts[1].pk not in [e.user_id for e in services.eligible_experts(review_round)]
    with pytest.raises(ValidationError, match="原因"):
        services.reassign_expert(a2, admin_user, experts[3].pk, "")
    new = services.reassign_expert(a2, admin_user, experts[3].pk, "原专家回避")
    assert new.slot == 2 and new.replaces_id == a2.id
    review_round.refresh_from_db()
    assert review_round.status == ReviewRound.STATUS_REVIEWING
    with pytest.raises(ValidationError):
        services.reassign_expert(a1, admin_user, experts[1].pk, "调整")  # 拒绝过本轮的专家不可再分配
    # 更换评审中的专家：原分配撤销并通知
    fifth = make_expert(admin_user, "周评审", "software", username="rev5")
    services.reassign_expert(a1, admin_user, fifth.pk, "逾期未评审")
    a1.refresh_from_db()
    assert a1.status == "cancelled" and a1.cancel_reason == "逾期未评审"
    assert Notification.objects.filter(recipient=experts[0], kind="review.revoke").exists()
    assert review_round.assignments.filter(status__in=ExpertAssignment.ACTIVE_STATUSES).count() == 3


def test_opinions_submit_and_remuneration(enterprise, admin_user, experts):
    config = SystemConfig.get()
    config.expert_fee_default = 1200
    config.save()
    project, assessment, review_round = start_review(enterprise, admin_user, experts)
    a1, a2, a3 = review_round.assignments.order_by("slot")
    first = services.task_items(a1)[0]
    services.save_opinions(a1, experts[0], {first.id: {"verdict": "fail", "suggestion": "", "comment": ""}})
    with pytest.raises(ValidationError, match="未给出"):
        services.submit_opinions(a1, experts[0])
    data = {ri.id: {"verdict": "pass"} for ri in services.task_items(a1)}
    data[first.id] = {"verdict": "fail"}
    services.save_opinions(a1, experts[0], data)
    with pytest.raises(ValidationError, match="满足情况评价或支撑材料评价"):
        services.submit_opinions(a1, experts[0])
    opine(a1, fail_codes=[first.check_item.code])
    a1.refresh_from_db()
    assert a1.status == "submitted"
    record = RemunerationRecord.objects.get(assignment=a1)
    assert record.amount == 1200 and record.item_count == review_round.items.count() and not record.is_settled
    with pytest.raises(ValidationError):
        services.save_opinions(a1, experts[0], data)
    opine(a2)
    review_round.refresh_from_db()
    assert review_round.status == ReviewRound.STATUS_REVIEWING
    opine(a3, fail_codes=[first.check_item.code])
    review_round.refresh_from_db()
    assert review_round.status == ReviewRound.STATUS_RULING
    assert Notification.objects.filter(recipient=admin_user, kind="review.ready").exists()
    # 专家操作不进入条目修改记录
    assert not AuditLog.objects.filter(model_name="CheckItem", actor__in=experts).exists()


def _ruled_round(enterprise, admin_user, experts):
    """第 2 级两条不通过：一条三位专家一致，一条只有席位 1 判不通过（意见不一致）。"""
    project, assessment, review_round = start_review(enterprise, admin_user, experts)
    level2 = [ri for ri in services.task_items(review_round.assignments.first()) if ri.check_item.level == 2]
    split, unanimous = level2[0].check_item, level2[1].check_item
    a1, a2, a3 = review_round.assignments.order_by("slot")
    opine(a1, fail_codes=[split.code, unanimous.code])
    opine(a2, fail_codes=[unanimous.code])
    opine(a3, fail_codes=[unanimous.code])
    review_round.refresh_from_db()
    return project, assessment, review_round, split, unanimous


def _finalize_with_level2_failures(enterprise, admin_user, experts):
    project, assessment, review_round, split, unanimous = _ruled_round(enterprise, admin_user, experts)
    services.confirm_unanimous(review_round, admin_user)
    services.rule_item(review_round.items.get(check_item=split), admin_user, "fail", reason="佐证不足", feedback="请补充需求评审记录")
    services.rule_item(review_round.items.get(check_item=unanimous), admin_user, "fail", feedback="请补充软件测试报告")
    services.finalize_round(review_round, admin_user, rectify_days=10, summary="总体较好")
    review_round.refresh_from_db()
    return project, assessment, review_round, split, unanimous


def test_ruling_rules_and_finalize_maps_levels(enterprise, admin_user, experts):
    project, assessment, review_round, split, unanimous = _ruled_round(enterprise, admin_user, experts)
    split_item = review_round.items.get(check_item=split)
    fail_item = review_round.items.get(check_item=unanimous)
    count = services.confirm_unanimous(review_round, admin_user)
    assert count == review_round.items.count() - 1
    split_item.refresh_from_db()
    assert split_item.final_result == ""
    with pytest.raises(ValidationError, match="未终审"):
        services.finalize_round(review_round, admin_user)
    with pytest.raises(ValidationError, match="裁定理由"):
        services.rule_item(split_item, admin_user, "pass")
    services.rule_item(split_item, admin_user, "pass", reason="佐证充分，采纳多数意见")
    split_item.refresh_from_db()
    assert split_item.final_basis == RoundItem.BASIS_RULING
    # 一致不通过的条目缺整改要求，不能提交终审
    with pytest.raises(ValidationError, match="整改要求"):
        services.finalize_round(review_round, admin_user)
    with pytest.raises(ValidationError, match="整改要求"):
        services.rule_item(fail_item, admin_user, "fail")
    services.rule_item(fail_item, admin_user, "fail", feedback="请补充软件测试报告")
    fail_item.refresh_from_db()
    assert fail_item.final_basis == RoundItem.BASIS_UNANIMOUS
    # 推翻一致意见必须写理由
    other = review_round.items.exclude(pk__in=[split_item.pk, fail_item.pk]).first()
    with pytest.raises(ValidationError, match="裁定理由"):
        services.rule_item(other, admin_user, "fail", feedback="补充")
    services.finalize_round(review_round, admin_user, rectify_days=10, summary="总体较好")
    review_round.refresh_from_db()
    assert review_round.status == ReviewRound.STATUS_RECTIFYING
    assert (review_round.rectify_due_at - timezone.now()).days in (9, 10)
    # 第 1 级全部通过 → 审核通过；第 2 级有不通过 → 退回；第 3 级虽全部通过但不连续 → 仍待审核
    assert _levels(assessment) == {
        1: ("passed", "expert"),
        2: ("returned", "expert"),
        3: ("submitted", "direct"),
    }
    assert eval_services.get_achieved_level(assessment) == 1
    note = Notification.objects.get(recipient=enterprise, kind="review.rectify")
    assert note.email_pending and note.category == "todo" and "第 2 级有 1 条需整改" in note.title
    assert Notification.objects.filter(recipient=enterprise, kind="level.pass", title__contains="第 1 级").exists()
    # 整改期间第三方评价组不写入专家姓名
    assert not project.team_members.filter(group=EvaluationTeamMember.GROUP_THIRD).exists()


def test_rectify_lock_resubmit_and_rereview(enterprise, admin_user, experts):
    project, assessment, review_round, split, unanimous = _finalize_with_level2_failures(enterprise, admin_user, experts)
    passed_in_level2 = assessment.items.filter(level=2).exclude(id__in=[split.id, unanimous.id]).first()
    level1_item = assessment.items.filter(level=1).first()
    # 只有不通过的条目可以修改和补充佐证；同级已通过专家评审的条目冻结
    with pytest.raises(ValidationError, match="已通过专家评审"):
        eval_services.evaluate_check_item(passed_in_level2, enterprise, {"result": "satisfied", "statement": "改"})
    with pytest.raises(ValidationError):
        upload_evidence_file(passed_in_level2, enterprise, _pdf())
    with pytest.raises(ValidationError, match="已审核通过"):
        eval_services.evaluate_check_item(level1_item, enterprise, {"result": "satisfied", "statement": "改"})
    for item in (split, unanimous):
        eval_services.evaluate_check_item(item, enterprise, {"result": "satisfied", "statement": "整改后的说明"})
    upload_evidence_file(unanimous, enterprise, _pdf("新增测试报告.pdf"))
    # 整改期间不能再指派专家，需等企业重新提交
    assert services.start_blockers(assessment) == ["上一轮专家评审的整改尚未全部提交"]

    # 企业照常“提交审核”即视为提交整改
    eval_services.submit_levels(assessment, enterprise, 2)
    review_round.refresh_from_db()
    assert review_round.status == ReviewRound.STATUS_CLOSED and review_round.outcome == ReviewRound.OUTCOME_RECTIFIED
    assert review_round.rectified_at
    assert Notification.objects.filter(recipient=admin_user, kind="level.submit").count() == 2
    assert AuditLog.objects.filter(action="review.resubmit").exists()

    # 管理员决定复评：沿用原专家组，只复评上一轮未通过的条目
    assert [a.expert for a in services.previous_experts(assessment)] == experts[:3]
    new_round = services.start_expert_review(assessment, admin_user, reuse_experts=True)
    assert new_round.number == 2 and new_round.status == ReviewRound.STATUS_REVIEWING
    assert set(new_round.items.values_list("check_item_id", flat=True)) == {split.id, unanimous.id}
    assert all(a.status == "accepted" and a.pledged_at for a in new_round.assignments.all())
    assert Notification.objects.filter(recipient=experts[0], kind="review.rereview", email_pending=True).exists()
    # 第 1 轮留档不随企业修改而改变；第 2 轮留档是修改后的内容
    old = review_round.items.get(check_item=unanimous)
    new = new_round.items.get(check_item=unanimous)
    assert old.snap_statement == "已核实" and new.snap_statement == "整改后的说明"
    assert len(new.snap_evidence) == len(old.snap_evidence) + 1

    for assignment in new_round.assignments.all():
        opine(assignment)
    new_round.refresh_from_db()
    services.confirm_unanimous(new_round, admin_user)
    services.finalize_round(new_round, admin_user)
    new_round.refresh_from_db()
    assert new_round.outcome == ReviewRound.OUTCOME_PASSED
    # 第 2 级复评通过后，之前已全部通过的第 3 级连同通过
    assert _levels(assessment) == {lv: ("passed", "expert") for lv in (1, 2, 3)}
    assert eval_services.get_achieved_level(assessment) == 3
    # 第三方评价组按席位自动填入（席位 1 为组长）
    third = list(project.team_members.filter(group=EvaluationTeamMember.GROUP_THIRD).order_by("order"))
    assert [m.role for m in third] == ["组长", "成员", "成员"] and third[0].name == "赵评审"
    assert Notification.objects.filter(recipient=enterprise, kind="level.pass", title__contains="已通过专家评审").count() == 2
    assert Notification.objects.filter(recipient=experts[1], kind="review.closed_for_expert").exists()
    # 结束后可以出报告
    assert eval_services.ready_for_report(assessment)
    EvaluationTeamMember.objects.create(project=project, group=EvaluationTeamMember.GROUP_SELF, role="组长", name="张工")
    from tests.report_enterprise_fixtures import seed_enterprise_report_files

    seed_enterprise_report_files(project, enterprise)
    report = generate_report(project, Report.TYPE_TRL, admin_user)
    assert report.trl_level == 3


def test_after_rectify_admin_may_self_review_or_pick_new_experts(enterprise, admin_user, experts):
    project, assessment, review_round, split, unanimous = _finalize_with_level2_failures(enterprise, admin_user, experts)
    for item in (split, unanimous):
        eval_services.evaluate_check_item(item, enterprise, {"result": "satisfied", "statement": "整改后的说明"})
    eval_services.submit_levels(assessment, enterprise, 2)
    # 重新选择专家：新一轮从分配专家开始，范围仍只是上一轮未通过的条目
    new_round = services.start_expert_review(assessment, admin_user, reuse_experts=False)
    assert new_round.status == ReviewRound.STATUS_ASSIGNING and new_round.items.count() == 2
    assert not new_round.assignments.exists()

    # 另一个项目：整改后管理员自行审核通过
    other_enterprise_project, other, other_round, s2, u2 = _second_project_rectified(enterprise, admin_user, experts)
    eval_services.pass_levels(other, admin_user, 3)
    assert _levels(other) == {1: ("passed", "expert"), 2: ("passed", "direct"), 3: ("passed", "direct")}
    assert eval_services.ready_for_report(other)


def _second_project_rectified(enterprise, admin_user, experts):
    project, assessment, review_round, split, unanimous = _finalize_with_level2_failures(enterprise, admin_user, experts)
    for item in (split, unanimous):
        eval_services.evaluate_check_item(item, enterprise, {"result": "satisfied", "statement": "整改后的说明"})
    eval_services.submit_levels(assessment, enterprise, 2)
    return project, assessment, review_round, split, unanimous


def test_single_blind(client, enterprise, other_enterprise, admin_user, experts):
    project, assessment, review_round = start_review(enterprise, admin_user, experts)
    a1, a2, a3 = review_round.assignments.order_by("slot")
    items = services.task_items(a1)
    opine(a1, fail_codes=[items[0].check_item.code], extra="甲")
    names = ["赵评审", "钱评审", "孙评审"]

    # 专家看不到其他专家的任务和意见
    client.force_login(experts[1])
    assert client.get(f"/reviews/tasks/{a1.id}/").status_code == 404
    page = client.get(f"/reviews/tasks/{a2.id}/").content.decode()
    assert MARK not in page and "赵评审" not in page
    # 专家不能进入项目页、报告页
    assert client.get(f"/projects/{project.id}/trl/").status_code == 404
    assert client.get(f"/reviews/assessments/{assessment.id}/history/").status_code == 404
    assert client.get(f"/reviews/rounds/{review_round.id}/").status_code == 403
    # 专家只能下载本轮留档中的佐证
    evidence_id = review_round.items.exclude(snap_evidence=[]).first().snap_evidence[0]["id"]
    assert client.get(f"/reviews/tasks/{a2.id}/evidence/{evidence_id}/").status_code == 200
    other_project = make_project(other_enterprise, targets={TRL: 1})
    other_evidence = upload_evidence_file(other_project.get_assessment(TRL).items.first(), other_enterprise, _pdf())
    assert client.get(f"/reviews/tasks/{a2.id}/evidence/{other_evidence.id}/").status_code == 404

    opine(a2, extra="乙")
    opine(a3, fail_codes=[items[0].check_item.code], extra="丙")
    review_round.refresh_from_db()
    services.confirm_unanimous(review_round, admin_user)
    split = review_round.items.get(check_item=items[0].check_item)
    services.rule_item(split, admin_user, "fail", reason="多数专家判不通过", feedback="请补充第三方测试报告")
    services.finalize_round(review_round, admin_user)

    # 企业：评价页、评审记录、收件箱都不出现专家姓名和专家原话
    client.force_login(enterprise)
    for url in [
        f"/projects/{project.id}/trl/",
        f"/projects/{project.id}/trl/?level=1",
        f"/reviews/assessments/{assessment.id}/history/",
        "/inbox/",
    ]:
        content = client.get(url).content.decode()
        assert "请补充第三方测试报告" in content or url == "/inbox/", url
        assert MARK not in content, url
        assert not any(name in content for name in names), url
        assert "多数专家判不通过" not in content, url  # 裁定理由只对评价机构可见
    for url in [f"/reviews/rounds/{review_round.id}/", "/reviews/", f"/reviews/tasks/{a1.id}/"]:
        assert client.get(url).status_code in (403, 404), url
    assert not Notification.objects.filter(recipient=enterprise, body__contains=MARK).exists()
    for note in Notification.objects.filter(recipient=enterprise):
        assert not any(name in note.title + note.body for name in names)

    # 管理员能并排看到三位专家的意见
    client.force_login(admin_user)
    content = client.get(f"/reviews/rounds/{review_round.id}/").content.decode()
    assert all(name in content for name in names) and MARK in content


def test_delete_project_cancels_pending_tasks(enterprise, admin_user, experts):
    project, assessment, review_round = start_review(enterprise, admin_user, experts)
    opine(review_round.assignments.get(slot=1))
    eval_services.delete_project(project, admin_user, "重复申报")
    statuses = dict(review_round.assignments.values_list("slot", "status"))
    assert statuses == {1: "submitted", 2: "cancelled", 3: "cancelled"}
    assert Notification.objects.filter(recipient=experts[1], kind="review.revoke").exists()
    assert not Notification.objects.filter(recipient=experts[0], kind="review.revoke").exists()


def test_review_pages_render_for_each_role(client, enterprise, admin_user, experts):
    from tests.test_accounts_access import FORBIDDEN_WORDS

    project, assessment, review_round = start_review(enterprise, admin_user, experts)
    a1 = review_round.assignments.get(slot=1)
    pages = {
        admin_user: [
            "/reviews/",
            f"/reviews/rounds/{review_round.id}/",
            f"/reviews/rounds/{review_round.id}/assign/",
            f"/reviews/assignments/{a1.id}/reassign/",
            f"/reviews/assessments/{assessment.id}/history/",
            f"/projects/{project.id}/trl/",
            "/audit/?action=review.",
            "/trl/",
        ],
        enterprise: [f"/projects/{project.id}/trl/", f"/reviews/assessments/{assessment.id}/history/", "/trl/"],
        experts[0]: ["/reviews/tasks/", f"/reviews/tasks/{a1.id}/", f"/reviews/tasks/{a1.id}/?level=2", "/inbox/"],
    }
    for user, urls in pages.items():
        client.force_login(user)
        for url in urls:
            response = client.get(url)
            assert response.status_code == 200, (user.username, url)
            found = FORBIDDEN_WORDS.search(response.content.decode())
            assert not found, (user.username, url, found and found.group(0))
    # 通过页面完成一次保存和提交
    client.force_login(experts[0])
    post = {"level": "1"}
    for ri in services.task_items(a1):
        post[f"verdict-{ri.id}"] = "pass"
    client.post(f"/reviews/tasks/{a1.id}/save/", post)
    response = client.post(f"/reviews/tasks/{a1.id}/submit/", {"confirm": "1"})
    assert response.status_code == 302
    a1.refresh_from_db()
    assert a1.status == "submitted"
