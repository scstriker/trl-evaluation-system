"""专家评审流程：企业申报 → 管理员分配 3 位专家 → 专家接受（签承诺）并逐条评审 → 管理员终审 → 整改 → 复评。

- 专家之间互不可见；企业只看管理员终审后的结论和整改要求，看不到专家姓名和专家原话（单盲）。
- 专家意见不一致或管理员推翻一致意见时，必须写裁定理由；判不通过必须写整改要求。
- 终审后按“自第 1 级起全部通过的连续级别”写入逐级审核记录（来源记为专家评审），
  达成等级、报告生成、导出申请沿用原流程。
- 专家的操作留痕记在评审分配 / 终审条目上，不进入条目修改记录，避免向企业暴露专家身份。
"""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SystemConfig
from apps.accounts.permissions import can_review, is_project_owner
from apps.audit.models import AuditLog
from apps.evaluations.models import Assessment, CheckItem, EvaluationTeamMember, LevelReview
from apps.experts.models import ExpertProfile
from apps.notifications.models import CATEGORY_RESULT, CATEGORY_TASK, CATEGORY_TODO
from apps.notifications.services import notify, notify_admins, remind_once
from apps.reviews.models import ExpertAssignment, ExpertOpinion, RemunerationRecord, ReviewRound, RoundItem

SLOTS = (1, 2, 3)
PASS = RoundItem.RESULT_PASS
FAIL = RoundItem.RESULT_FAIL

PLEDGE_VERSION = "2026.1"
PLEDGE_CLAUSES = [
    "对评审中接触的项目资料、企业技术信息和评审意见严格保密，不向第三方披露，不用于评审以外的用途。",
    "与被评企业不存在任职、持股、近三年合作或其他可能影响公正评审的利害关系；如存在，应主动回避并拒绝本任务。",
    "独立、客观、公正地逐条给出评审意见，不与其他专家或被评企业私下沟通评审内容。",
    "评审结束后，按评价机构要求删除或交还下载的评审资料。",
]

# 专家评审方式下等级阶梯的附加状态
STATE_EXPERT_REVIEW = "expert_review"
STATE_RECTIFY = "rectify"
STATE_NOT_PASSED = "not_passed"
STATE_LABELS = {STATE_EXPERT_REVIEW: "专家评审中", STATE_RECTIFY: "需整改", STATE_NOT_PASSED: "未通过"}


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------


def _log(actor, action, model_name, object_id, object_repr, project, before=None, after=None):
    AuditLog.objects.create(
        actor=actor,
        action=action,
        model_name=model_name,
        object_id=str(object_id),
        object_repr=object_repr,
        project=project,
        before=before,
        after=after,
    )


def round_link(review_round):
    return reverse("reviews:round_detail", args=[review_round.id])


def task_link(assignment):
    return reverse("reviews:task_detail", args=[assignment.id])


def enterprise_link(assessment):
    url = reverse("evaluations:assessment_detail", args=[assessment.project_id, assessment.system])
    return f"{url}#expert-panel"


def expert_name(user):
    profile = getattr(user, "expert_profile", None)
    return profile.name if profile else user.username


def _round_label(review_round):
    sys = review_round.assessment.sys
    return f"{sys.name}第 {review_round.number} 轮"


def _ensure_admin(actor):
    if not can_review(actor):
        raise PermissionDenied("专家评审由评价机构管理员组织。")


def _ensure_project_alive(project):
    if project.is_deleted:
        raise ValidationError("该项目已删除。")


def _parse_days(value, default, upper, label):
    try:
        days = int(value) if value not in (None, "") else int(default)
    except (TypeError, ValueError):
        raise ValidationError(f"{label}须为整数天数。")
    if not 1 <= days <= upper:
        raise ValidationError(f"{label}须在 1～{upper} 天之间。")
    return days


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def rounds_of(assessment):
    return list(assessment.review_rounds.order_by("number"))


def latest_round(assessment):
    return assessment.review_rounds.order_by("-number").first()


def latest_results(assessment):
    """每个条目最近一次终审结论：{check_item_id: RoundItem}。后一轮的结论覆盖前一轮。"""
    results = {}
    for round_item in (
        RoundItem.objects.filter(round__assessment=assessment).exclude(final_result="").order_by("round__number")
    ):
        results[round_item.check_item_id] = round_item
    return results


def active_assignments(review_round):
    return list(
        review_round.assignments.filter(status__in=ExpertAssignment.ACTIVE_STATUSES)
        .select_related("expert__expert_profile__specialty")
        .order_by("slot")
    )


def free_slots(review_round):
    taken = {a.slot for a in review_round.assignments.filter(status__in=ExpertAssignment.ACTIVE_STATUSES)}
    return [slot for slot in SLOTS if slot not in taken]


def refresh_round_status(review_round):
    """组建 / 评审 / 待终审三个状态由三个席位的分配状态推导。"""
    if review_round.status in (ReviewRound.STATUS_RECTIFYING, ReviewRound.STATUS_CLOSED):
        return review_round.status
    active = list(review_round.assignments.filter(status__in=ExpertAssignment.ACTIVE_STATUSES))
    if len(active) < len(SLOTS):
        status = ReviewRound.STATUS_ASSIGNING
    elif all(a.status == ExpertAssignment.STATUS_SUBMITTED for a in active):
        status = ReviewRound.STATUS_RULING
    else:
        status = ReviewRound.STATUS_REVIEWING
    if status != review_round.status:
        review_round.status = status
        review_round.save(update_fields=["status"])
    return status


def fill_progress(assessment):
    total = assessment.items.count()
    missing = assessment.items.filter(result=CheckItem.RESULT_UNEVALUATED).count()
    return {"total": total, "completed": total - missing, "missing": missing}


# ---------------------------------------------------------------------------
# 与评价流程的衔接（evaluations.services 在专家评审方式下调用）
# ---------------------------------------------------------------------------


def editable_item_ids(assessment):
    """企业可编辑的条目。None 表示全部可编辑（尚未申报）；整改中只能改上一轮判为不通过的条目。"""
    review_round = latest_round(assessment)
    if review_round is None:
        return None
    if review_round.status == ReviewRound.STATUS_RECTIFYING:
        return set(review_round.items.filter(final_result=FAIL).values_list("check_item_id", flat=True))
    return set()


def item_lock_reason(item):
    review_round = latest_round(item.assessment)
    if review_round is None:
        return None
    if review_round.status in ReviewRound.LOCKED_STATUSES:
        return f"第 {review_round.number} 轮专家评审进行中，条目已冻结，不能修改。"
    if review_round.status == ReviewRound.STATUS_RECTIFYING:
        if review_round.items.filter(check_item=item, final_result=FAIL).exists():
            return None
        return "该条目已通过专家评审，整改期间只能修改未通过的条目。"
    return "专家评审已结束，内容已冻结。"


def level_overrides(assessment):
    """等级阶梯：评审中的级别显示“专家评审中”，有不通过条目的显示“需整改 / 未通过”，全部通过的显示“已通过”。"""
    review_round = latest_round(assessment)
    if review_round is None:
        return {}
    results = latest_results(assessment)
    in_round = set()
    if review_round.status in ReviewRound.LOCKED_STATUSES:
        in_round = set(review_round.items.values_list("check_item_id", flat=True))
    by_level = {}
    for item_id, level in assessment.items.values_list("id", "level"):
        by_level.setdefault(level, []).append(item_id)
    states = {}
    for level, ids in by_level.items():
        if any(i in in_round for i in ids):
            states[level] = STATE_EXPERT_REVIEW
        elif all(i in results and results[i].final_result == PASS for i in ids):
            states[level] = LevelReview.STATUS_PASSED
        elif any(i in results and results[i].final_result == FAIL for i in ids):
            states[level] = STATE_RECTIFY if review_round.status == ReviewRound.STATUS_RECTIFYING else STATE_NOT_PASSED
    return states


def ready_for_report(assessment):
    """专家评审方式：评审已结束且至少达成 1 级。"""
    from apps.evaluations.services import get_achieved_level

    review_round = latest_round(assessment)
    return bool(review_round and not review_round.is_open and get_achieved_level(assessment) >= 1)


def report_block_reason(assessment):
    from apps.evaluations.services import get_achieved_level

    sys = assessment.sys
    review_round = latest_round(assessment)
    if review_round is None:
        return f"{sys.name}评价采用专家评审方式，尚未申报专家评审"
    if review_round.is_open:
        return f"{sys.name}专家评审尚未结束（第 {review_round.number} 轮{review_round.get_status_display()}）"
    if get_achieved_level(assessment) < 1:
        return f"{sys.name}专家评审已结束，但第 1 级未通过，没有达成等级"
    return None


def next_step(assessment, for_admin):
    from apps.evaluations.services import get_achieved_level

    sys = assessment.sys
    review_round = latest_round(assessment)
    if review_round is None:
        progress = fill_progress(assessment)
        if for_admin:
            return "企业填写中（专家评审方式）", "neutral"
        if progress["missing"]:
            return f"请填写全部等级（剩 {progress['missing']} 条）后申报专家评审", "neutral"
        return "全部等级已填完，可申报专家评审", "info"
    n = review_round.number
    if review_round.status == ReviewRound.STATUS_ASSIGNING:
        if for_admin:
            return f"第 {n} 轮：待分配专家（还差 {len(free_slots(review_round))} 位）", "warning"
        return "已申报专家评审，等待组建专家组", "info"
    if review_round.status == ReviewRound.STATUS_REVIEWING:
        if for_admin:
            active = active_assignments(review_round)
            done = sum(1 for a in active if a.status == ExpertAssignment.STATUS_SUBMITTED)
            overdue = sum(1 for a in active if a.is_overdue)
            text = f"第 {n} 轮：专家评审中（已提交 {done}/3）"
            return (text + f"，{overdue} 位逾期", "warning") if overdue else (text, "info")
        return "专家评审中", "info"
    if review_round.status == ReviewRound.STATUS_RULING:
        return (f"第 {n} 轮：专家意见已齐，待终审", "warning") if for_admin else ("专家评审中", "info")
    if review_round.status == ReviewRound.STATUS_RECTIFYING:
        due = timezone.localtime(review_round.rectify_due_at)
        count = review_round.items.filter(final_result=FAIL).count()
        if for_admin:
            return (f"整改中，截止 {due:%m-%d}" + ("（已逾期）" if review_round.rectify_overdue else ""), "warning" if review_round.rectify_overdue else "neutral")
        return f"需整改 {count} 条，请于 {due:%m-%d} 前提交整改", "warning"
    achieved = get_achieved_level(assessment)
    if for_admin:
        return (f"专家评审结束（达成 {sys.abbr} {achieved}），可生成报告", "success") if achieved else ("专家评审结束，未达成等级", "neutral")
    return f"专家评审结束（达成 {sys.abbr} {achieved}），等待出具报告", "success"


def list_bucket(assessment):
    """项目列表分组：评审进行中计入“待审核”，整改中计入“退回”，结束后可出报告计入“待出报告”。"""
    review_round = latest_round(assessment)
    if review_round is None:
        return "draft"
    if review_round.status in ReviewRound.LOCKED_STATUSES:
        return "submitted"
    if review_round.status == ReviewRound.STATUS_RECTIFYING:
        return "returned"
    return "ready" if ready_for_report(assessment) else "returned"


# ---------------------------------------------------------------------------
# 评审方式与申报（企业）
# ---------------------------------------------------------------------------


def can_switch_mode(assessment):
    return (
        assessment.status == Assessment.STATUS_IN_PROGRESS
        and not assessment.level_reviews.exists()
        and not assessment.review_rounds.exists()
    )


@transaction.atomic
def set_review_mode(assessment, actor, mode):
    project = assessment.project
    if not (is_project_owner(actor, project) or can_review(actor)):
        raise PermissionDenied("只有申报企业或评价机构可以选择评审方式。")
    if mode not in dict(Assessment.MODE_CHOICES):
        raise ValidationError("评审方式无效。")
    if not can_switch_mode(assessment):
        raise ValidationError("已提交审核或已申报专家评审，不能再更改评审方式。")
    if assessment.review_mode == mode:
        return assessment
    before = {"review_mode": assessment.review_mode}
    assessment.review_mode = mode
    assessment.save(update_fields=["review_mode", "updated_at"])
    _log(actor, "review.mode", "Assessment", assessment.id, f"{assessment.sys.name}评价", project, before, {"review_mode": mode})
    return assessment


def _snapshot(review_round, items):
    RoundItem.objects.bulk_create(
        RoundItem(
            round=review_round,
            check_item=item,
            snap_result=item.result,
            snap_statement=item.statement,
            snap_support_info=item.support_info,
            snap_evidence=[
                {"id": str(e.id), "name": e.original_filename, "sha256": e.sha256, "size": e.size_bytes}
                for e in item.evidence_files.all()
                if not e.is_deleted
            ],
        )
        for item in items.prefetch_related("evidence_files").order_by("order_index")
    )


@transaction.atomic
def apply_expert_review(assessment, actor):
    project = assessment.project
    if not is_project_owner(actor, project):
        raise PermissionDenied("只有申报企业可以申报专家评审。")
    _ensure_project_alive(project)
    sys = assessment.sys
    if assessment.status != Assessment.STATUS_IN_PROGRESS:
        raise ValidationError(f"{sys.name}评价已出具报告，不能再申报。")
    if assessment.level_reviews.exists():
        raise ValidationError("该评价已按管理员逐级审核方式提交过，不能改为专家评审。")
    if assessment.review_rounds.exists():
        raise ValidationError("该评价已申报过专家评审。")
    progress = fill_progress(assessment)
    if progress["missing"]:
        raise ValidationError(
            f"还有 {progress['missing']} 条未填写。专家评审需要先填写全部等级（{sys.abbr} 1~{sys.max_level}）的条目。"
        )
    assessment.review_mode = Assessment.MODE_EXPERT
    assessment.save(update_fields=["review_mode", "updated_at"])
    config = SystemConfig.get()
    review_round = ReviewRound.objects.create(
        assessment=assessment, number=1, submitted_by=actor, review_days=config.expert_review_days
    )
    _snapshot(review_round, assessment.items.all())
    _log(actor, "review.apply", "ReviewRound", review_round.id, f"{sys.abbr} 第 1 轮", project, after={"items": progress["total"]})
    notify_admins(
        "review.apply",
        f"{project.applicant}申报了{sys.name}专家评审，待分配专家",
        body=f"项目：{project.name}\n技术领域：{project.specialty.name if project.specialty else '未选择'}\n评审条目：{progress['total']} 条",
        link=round_link(review_round),
        project=project,
        category=CATEGORY_TODO,
    )
    return review_round


# ---------------------------------------------------------------------------
# 分配专家（管理员）
# ---------------------------------------------------------------------------


def conflict_hint(profile, project):
    """专家单位与申报企业名称相互包含时提示可能需要回避（由管理员判断）。"""
    org, applicant = profile.organization.strip(), project.applicant.strip()
    return bool(org and applicant and (org in applicant or applicant in org))


def eligible_experts(review_round, specialty=None, q=""):
    taken = set(
        review_round.assignments.filter(
            status__in=ExpertAssignment.ACTIVE_STATUSES + (ExpertAssignment.STATUS_DECLINED,)
        ).values_list("expert_id", flat=True)
    )
    experts = (
        ExpertProfile.objects.select_related("user", "specialty")
        .filter(user__is_active=True, in_service=True)
        .exclude(user_id__in=taken)
    )
    if specialty is not None:
        experts = experts.filter(specialty=specialty)
    experts = list(experts)
    if q:
        experts = [e for e in experts if any(q in text for text in [e.name, e.organization, e.title, *(e.tags or [])])]
    return experts


def _pending_load(user):
    return ExpertAssignment.objects.filter(expert=user, status__in=ExpertAssignment.PENDING_STATUSES).count()


def expert_loads(experts):
    return {e.user_id: _pending_load(e.user) for e in experts}


def _notify_new_task(assignment, project, review_round):
    notify(
        assignment.expert,
        "review.assign",
        f"新评审任务：{project.name}（{_round_label(review_round)}）",
        body=f"委托单位：{project.applicant}\n评审条目：{review_round.items.count()} 条\n截止时间："
        f"{timezone.localtime(assignment.due_at):%Y-%m-%d}\n请先阅读并确认保密与回避承诺，再接受任务。",
        link=task_link(assignment),
        project=project,
        category=CATEGORY_TASK,
        email=True,
    )


@transaction.atomic
def assign_experts(review_round, actor, expert_ids, review_days=None):
    _ensure_admin(actor)
    assessment = review_round.assessment
    project = assessment.project
    _ensure_project_alive(project)
    if review_round.status != ReviewRound.STATUS_ASSIGNING:
        raise ValidationError("当前轮次的专家组已组建，不需要再分配。如需更换，请在专家席位上操作“更换”。")
    slots = free_slots(review_round)
    try:
        ids = list(dict.fromkeys(int(i) for i in expert_ids))
    except (TypeError, ValueError):
        raise ValidationError("所选专家无效。")
    if len(ids) != len(slots):
        raise ValidationError(f"每轮固定 3 位专家，还需选择 {len(slots)} 位，当前选择了 {len(ids)} 位。")
    eligible = {e.user_id: e for e in eligible_experts(review_round)}
    invalid = [i for i in ids if i not in eligible]
    if invalid:
        raise ValidationError("所选专家中有账号停用、不在职或已在本轮评审中的，请重新选择。")
    days = _parse_days(review_days, review_round.review_days, 60, "专家评审期限")
    if days != review_round.review_days:
        review_round.review_days = days
        review_round.save(update_fields=["review_days"])
    due = timezone.now() + timedelta(days=days)
    created = []
    for slot, user_id in zip(slots, ids):
        assignment = ExpertAssignment.objects.create(
            round=review_round, expert=eligible[user_id].user, slot=slot, assigned_by=actor, due_at=due
        )
        created.append(assignment)
        _notify_new_task(assignment, project, review_round)
    _log(
        actor,
        "review.assign",
        "ReviewRound",
        review_round.id,
        f"{assessment.sys.abbr} 第 {review_round.number} 轮",
        project,
        after={"experts": [eligible[i].name for i in ids], "slots": slots, "days": days},
    )
    if refresh_round_status(review_round) == ReviewRound.STATUS_REVIEWING and remind_once(
        f"group_formed:{review_round.id}", "review.group_formed"
    ):
        notify(
            project.owner,
            "review.group_formed",
            f"{assessment.sys.name}专家组已组建（3 位专家），开始评审",
            body=f"项目：{project.name}\n第 {review_round.number} 轮评审期限 {days} 天。评审期间条目冻结，结果出来后会通知你。",
            link=enterprise_link(assessment),
            project=project,
        )
    return created


@transaction.atomic
def reassign_expert(assignment, actor, new_expert_id, reason):
    _ensure_admin(actor)
    review_round = assignment.round
    project = review_round.assessment.project
    _ensure_project_alive(project)
    if assignment.status not in (
        ExpertAssignment.STATUS_INVITED,
        ExpertAssignment.STATUS_ACCEPTED,
        ExpertAssignment.STATUS_DECLINED,
    ):
        raise ValidationError("只能更换尚未提交意见的专家。")
    if review_round.status not in (ReviewRound.STATUS_ASSIGNING, ReviewRound.STATUS_REVIEWING):
        raise ValidationError("当前轮次不能更换专家。")
    if assignment.round.assignments.filter(replaces=assignment).exists():
        raise ValidationError("该席位已经更换过，请在新的分配上操作。")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("更换专家时必须填写原因。")
    eligible = {e.user_id: e for e in eligible_experts(review_round)}
    try:
        new_id = int(new_expert_id)
    except (TypeError, ValueError):
        raise ValidationError("请选择接替的专家。")
    if new_id not in eligible:
        raise ValidationError("所选专家不可分配（账号停用、不在职或已在本轮评审中）。")
    now = timezone.now()
    if assignment.status != ExpertAssignment.STATUS_DECLINED:
        assignment.status = ExpertAssignment.STATUS_CANCELLED
        assignment.cancelled_at = now
        assignment.cancel_reason = reason
        assignment.save(update_fields=["status", "cancelled_at", "cancel_reason"])
        notify(
            assignment.expert,
            "review.revoke",
            f"评审任务已收回：{project.name}（{_round_label(review_round)}）",
            body="评价机构已将该任务改派给其他专家，你无需继续评审。感谢支持。",
            project=project,
            category=CATEGORY_TASK,
        )
    new = ExpertAssignment.objects.create(
        round=review_round,
        expert=eligible[new_id].user,
        slot=assignment.slot,
        assigned_by=actor,
        due_at=now + timedelta(days=review_round.review_days),
        replaces=assignment,
    )
    _notify_new_task(new, project, review_round)
    _log(
        actor,
        "review.reassign",
        "ExpertAssignment",
        assignment.id,
        f"席位 {assignment.slot}：{expert_name(assignment.expert)} → {eligible[new_id].name}",
        project,
        after={"reason": reason},
    )
    refresh_round_status(review_round)
    return new


@transaction.atomic
def remind_expert(assignment, actor):
    _ensure_admin(actor)
    if assignment.status not in ExpertAssignment.PENDING_STATUSES:
        raise ValidationError("该专家已提交意见或任务已结束，无需催办。")
    project = assignment.round.assessment.project
    assignment.remind_count += 1
    assignment.last_reminded_at = timezone.now()
    assignment.save(update_fields=["remind_count", "last_reminded_at"])
    due = timezone.localtime(assignment.due_at)
    late = f"已逾期 {assignment.overdue_days} 天" if assignment.is_overdue else f"截止 {due:%Y-%m-%d}"
    notify(
        assignment.expert,
        "review.remind",
        f"催办：请尽快完成「{project.name}」的评审（{late}）",
        body="评价机构提醒你尽快完成评审任务。如无法按期完成，请联系评价机构。",
        link=task_link(assignment),
        project=project,
        category=CATEGORY_TASK,
        email=True,
    )
    _log(actor, "review.remind", "ExpertAssignment", assignment.id, f"席位 {assignment.slot}", project, after={"count": assignment.remind_count})
    return assignment


@transaction.atomic
def extend_assignment(assignment, actor, days):
    _ensure_admin(actor)
    if assignment.status not in ExpertAssignment.PENDING_STATUSES:
        raise ValidationError("只能为未提交意见的专家延长期限。")
    days = _parse_days(days, 7, 30, "延长天数")
    project = assignment.round.assessment.project
    before = assignment.due_at
    assignment.due_at = max(assignment.due_at, timezone.now()) + timedelta(days=days)
    assignment.save(update_fields=["due_at"])
    notify(
        assignment.expert,
        "review.extend",
        f"评审期限已延长：{project.name}，新的截止时间 {timezone.localtime(assignment.due_at):%Y-%m-%d}",
        link=task_link(assignment),
        project=project,
        category=CATEGORY_TASK,
    )
    _log(
        actor,
        "review.extend",
        "ExpertAssignment",
        assignment.id,
        f"席位 {assignment.slot}",
        project,
        before={"due_at": before.isoformat()},
        after={"due_at": assignment.due_at.isoformat()},
    )
    return assignment


# ---------------------------------------------------------------------------
# 专家：接受 / 拒绝、逐条意见、提交
# ---------------------------------------------------------------------------


def _ensure_own(assignment, actor):
    if not actor or assignment.expert_id != actor.id:
        raise PermissionDenied("这不是分配给你的评审任务。")
    _ensure_project_alive(assignment.round.assessment.project)


@transaction.atomic
def respond_assignment(assignment, actor, accept, pledge=False, reason=""):
    _ensure_own(assignment, actor)
    if assignment.status != ExpertAssignment.STATUS_INVITED:
        raise ValidationError("该任务已处理过。")
    review_round = assignment.round
    project = review_round.assessment.project
    name = expert_name(actor)
    now = timezone.now()
    if accept:
        if not pledge:
            raise ValidationError("接受任务前请阅读并勾选保密与回避承诺。")
        assignment.status = ExpertAssignment.STATUS_ACCEPTED
        assignment.pledge_version = PLEDGE_VERSION
        assignment.pledged_at = now
        assignment.responded_at = now
        assignment.save(update_fields=["status", "pledge_version", "pledged_at", "responded_at"])
        _log(actor, "review.accept", "ExpertAssignment", assignment.id, f"席位 {assignment.slot}", project, after={"pledge": PLEDGE_VERSION})
        notify_admins(
            "review.accept",
            f"{name}已接受「{project.name}」{_round_label(review_round)}评审任务",
            link=round_link(review_round),
            project=project,
        )
    else:
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("拒绝任务时请填写原因（如需回避、时间冲突等）。")
        assignment.status = ExpertAssignment.STATUS_DECLINED
        assignment.decline_reason = reason[:255]
        assignment.responded_at = now
        assignment.save(update_fields=["status", "decline_reason", "responded_at"])
        _log(actor, "review.decline", "ExpertAssignment", assignment.id, f"席位 {assignment.slot}", project, after={"reason": reason})
        notify_admins(
            "review.decline",
            f"{name}拒绝了「{project.name}」{_round_label(review_round)}评审任务，需改派",
            body=f"拒绝原因：{reason}",
            link=round_link(review_round),
            project=project,
            category=CATEGORY_TODO,
        )
        refresh_round_status(review_round)
    return assignment


def task_items(assignment):
    return list(
        assignment.round.items.select_related("check_item").order_by("check_item__order_index")
    )


def opinions_by_item(assignment):
    return {o.round_item_id: o for o in assignment.opinions.all()}


@transaction.atomic
def save_opinions(assignment, actor, data, submit=False):
    """data: {round_item_id: {"verdict", "suggestion", "comment"}}。保存草稿时允许部分填写。"""
    _ensure_own(assignment, actor)
    if assignment.status != ExpertAssignment.STATUS_ACCEPTED:
        raise ValidationError("只有已接受、尚未提交的任务可以填写意见。")
    valid_ids = set(assignment.round.items.values_list("id", flat=True))
    for round_item_id, values in data.items():
        if round_item_id not in valid_ids:
            continue
        verdict = (values.get("verdict") or "").strip()
        if verdict not in ("", PASS, FAIL):
            raise ValidationError("评审结论无效。")
        ExpertOpinion.objects.update_or_create(
            assignment=assignment,
            round_item_id=round_item_id,
            defaults={
                "verdict": verdict,
                "suggestion": (values.get("suggestion") or "").strip(),
                "comment": (values.get("comment") or "").strip(),
            },
        )
    if submit:
        return submit_opinions(assignment, actor)
    return assignment


def opinion_gaps(assignment):
    """提交前检查：未给结论的条目、判不通过但没写点评或建议的条目。"""
    opinions = opinions_by_item(assignment)
    missing, unexplained = [], []
    for round_item in task_items(assignment):
        opinion = opinions.get(round_item.id)
        if not opinion or not opinion.verdict:
            missing.append(round_item)
        elif opinion.verdict == FAIL and not (opinion.comment or opinion.suggestion):
            unexplained.append(round_item)
    return missing, unexplained


@transaction.atomic
def submit_opinions(assignment, actor):
    _ensure_own(assignment, actor)
    if assignment.status != ExpertAssignment.STATUS_ACCEPTED:
        raise ValidationError("该任务不能提交（未接受或已提交）。")
    missing, unexplained = opinion_gaps(assignment)
    if missing:
        codes = "、".join(ri.check_item.code for ri in missing[:8]) + ("……" if len(missing) > 8 else "")
        raise ValidationError(f"还有 {len(missing)} 条未给出是否通过的结论：{codes}。")
    if unexplained:
        codes = "、".join(ri.check_item.code for ri in unexplained[:8]) + ("……" if len(unexplained) > 8 else "")
        raise ValidationError(f"判为不通过的条目须填写满足建议或点评：{codes}。")
    review_round = assignment.round
    assessment = review_round.assessment
    project = assessment.project
    now = timezone.now()
    assignment.status = ExpertAssignment.STATUS_SUBMITTED
    assignment.submitted_at = now
    assignment.save(update_fields=["status", "submitted_at"])
    opinions = list(assignment.opinions.all())
    RemunerationRecord.objects.create(
        assignment=assignment,
        expert=actor,
        project=project,
        project_name=project.name,
        system=assessment.system,
        round_number=review_round.number,
        item_count=len(opinions),
        amount=SystemConfig.get().expert_fee_default,
    )
    passed = sum(1 for o in opinions if o.verdict == PASS)
    _log(
        actor,
        "review.submit",
        "ExpertAssignment",
        assignment.id,
        f"席位 {assignment.slot} · 第 {review_round.number} 轮",
        project,
        after={"pass": passed, "fail": len(opinions) - passed},
    )
    done = review_round.assignments.filter(status=ExpertAssignment.STATUS_SUBMITTED).count()
    notify_admins(
        "review.submit",
        f"{expert_name(actor)}已提交「{project.name}」{_round_label(review_round)}评审意见（{done}/3）",
        body=f"通过 {passed} 条，不通过 {len(opinions) - passed} 条。",
        link=round_link(review_round),
        project=project,
    )
    if refresh_round_status(review_round) == ReviewRound.STATUS_RULING:
        notify_admins(
            "review.ready",
            f"「{project.name}」{_round_label(review_round)}三位专家意见已齐，可以终审",
            link=round_link(review_round),
            project=project,
            category=CATEGORY_TODO,
        )
    return assignment


# ---------------------------------------------------------------------------
# 终审（管理员）
# ---------------------------------------------------------------------------


def _consensus(cells):
    if len(cells) != len(SLOTS) or not all(c and c.verdict for c in cells):
        return ""
    verdicts = {c.verdict for c in cells}
    return verdicts.pop() if len(verdicts) == 1 else ""


def opinion_matrix(review_round):
    """终审对照表：按席位排列三位专家已提交的意见，标出是否一致。未提交的意见（草稿）不显示。"""
    submitted = list(
        review_round.assignments.filter(status=ExpertAssignment.STATUS_SUBMITTED)
        .select_related("expert__expert_profile")
        .order_by("slot")
    )
    by_item = {}
    for opinion in ExpertOpinion.objects.filter(assignment__in=submitted):
        by_item.setdefault(opinion.round_item_id, {})[opinion.assignment_id] = opinion
    rows = []
    for round_item in review_round.items.select_related("check_item").order_by("check_item__order_index"):
        cells = [by_item.get(round_item.id, {}).get(a.id) for a in submitted]
        verdicts = {c.verdict for c in cells if c and c.verdict}
        rows.append(
            {
                "item": round_item,
                "cells": cells,
                "consensus": _consensus(cells),
                "split": len(verdicts) > 1,
            }
        )
    return submitted, rows


def _ensure_ruling(review_round):
    if review_round.status != ReviewRound.STATUS_RULING:
        raise ValidationError("三位专家意见齐全后才能终审。")
    _ensure_project_alive(review_round.assessment.project)


@transaction.atomic
def confirm_unanimous(review_round, actor):
    _ensure_admin(actor)
    _ensure_ruling(review_round)
    now = timezone.now()
    count = 0
    for row in opinion_matrix(review_round)[1]:
        round_item = row["item"]
        if row["consensus"] and not round_item.final_result:
            round_item.final_result = row["consensus"]
            round_item.final_basis = RoundItem.BASIS_UNANIMOUS
            round_item.ruled_by = actor
            round_item.ruled_at = now
            round_item.save(update_fields=["final_result", "final_basis", "ruled_by", "ruled_at"])
            count += 1
    if count:
        _log(
            actor,
            "review.confirm_unanimous",
            "ReviewRound",
            review_round.id,
            f"第 {review_round.number} 轮",
            review_round.assessment.project,
            after={"count": count},
        )
    return count


@transaction.atomic
def rule_item(round_item, actor, result, reason="", feedback=""):
    _ensure_admin(actor)
    review_round = round_item.round
    _ensure_ruling(review_round)
    if result not in (PASS, FAIL):
        raise ValidationError("请选择终审结论：通过或不通过。")
    reason = (reason or "").strip()
    feedback = (feedback or "").strip()
    submitted = list(review_round.assignments.filter(status=ExpertAssignment.STATUS_SUBMITTED).order_by("slot"))
    opinions = {o.assignment_id: o for o in round_item.opinions.filter(assignment__in=submitted)}
    consensus = _consensus([opinions.get(a.id) for a in submitted])
    if consensus and result == consensus:
        basis = RoundItem.BASIS_UNANIMOUS
    else:
        basis = RoundItem.BASIS_RULING
        if not reason:
            raise ValidationError("专家意见不一致，或终审结论与专家一致意见不同，必须填写裁定理由。")
    if result == FAIL and not feedback:
        raise ValidationError("判为不通过时必须填写整改要求，企业据此整改。")
    before = {"result": round_item.final_result}
    round_item.final_result = result
    round_item.final_basis = basis
    round_item.ruling_reason = reason
    round_item.feedback = feedback if result == FAIL else ""
    round_item.ruled_by = actor
    round_item.ruled_at = timezone.now()
    round_item.save(update_fields=["final_result", "final_basis", "ruling_reason", "feedback", "ruled_by", "ruled_at"])
    _log(
        actor,
        "review.rule",
        "RoundItem",
        round_item.id,
        round_item.check_item.code,
        review_round.assessment.project,
        before,
        {"result": result, "basis": basis, "reason": reason},
    )
    return round_item


def sync_level_reviews(assessment, actor):
    """把“自第 1 级起全部通过的连续级别”写入逐级审核记录，返回达成等级。已写入的保留原通过时间。"""
    results = latest_results(assessment)
    by_level = {}
    for item_id, level in assessment.items.values_list("id", "level"):
        by_level.setdefault(level, []).append(item_id)
    first_round = assessment.review_rounds.order_by("number").first()
    achieved = 0
    now = timezone.now()
    for level in assessment.sys.levels:
        ids = by_level.get(level, [])
        if not ids or not all(i in results and results[i].final_result == PASS for i in ids):
            break
        LevelReview.objects.get_or_create(
            assessment=assessment,
            level=level,
            defaults={
                "status": LevelReview.STATUS_PASSED,
                "source": LevelReview.SOURCE_EXPERT,
                "submitted_by": first_round.submitted_by if first_round else None,
                "submitted_at": first_round.submitted_at if first_round else None,
                "reviewed_by": actor,
                "reviewed_at": now,
            },
        )
        achieved = level
    return achieved


def fill_third_team(project, review_round):
    """评审结束后，第三方评价组为空时按席位自动填入专家组（席位 1 为组长）。"""
    if project.team_members.filter(group=EvaluationTeamMember.GROUP_THIRD).exists():
        return 0
    assignments = review_round.assignments.filter(status=ExpertAssignment.STATUS_SUBMITTED).select_related(
        "expert__expert_profile__specialty"
    )
    start = project.team_members.count()
    count = 0
    for index, assignment in enumerate(assignments.order_by("slot")):
        profile = assignment.expert.expert_profile
        title = " / ".join(part for part in (profile.title, profile.position) if part)
        EvaluationTeamMember.objects.create(
            project=project,
            group=EvaluationTeamMember.GROUP_THIRD,
            order=start + index + 1,
            role="组长" if index == 0 else "成员",
            name=profile.name,
            title=title[:64],
            specialty=(profile.research or profile.specialty.name)[:128],
            org=profile.organization,
        )
        count += 1
    return count


@transaction.atomic
def finalize_round(review_round, actor, decision, rectify_days=None, summary=""):
    from apps.evaluations.services import get_achieved_level

    _ensure_admin(actor)
    _ensure_ruling(review_round)
    assessment = review_round.assessment
    project = assessment.project
    sys = assessment.sys
    unruled = review_round.items.filter(final_result="").count()
    if unruled:
        raise ValidationError(f"还有 {unruled} 条未终审，请逐条确认后再提交终审结果。")
    fails = list(review_round.items.filter(final_result=FAIL).select_related("check_item"))
    no_feedback = [ri.check_item.code for ri in fails if not ri.feedback]
    if no_feedback:
        raise ValidationError(f"以下不通过条目缺少整改要求：{'、'.join(no_feedback[:8])}。")
    summary = (summary or "").strip()
    now = timezone.now()
    if not fails:
        review_round.status = ReviewRound.STATUS_CLOSED
        review_round.outcome = ReviewRound.OUTCOME_PASSED
    elif decision == "rectify":
        days = _parse_days(rectify_days, SystemConfig.get().rectify_days, 90, "整改期限")
        review_round.status = ReviewRound.STATUS_RECTIFYING
        review_round.rectify_due_at = now + timedelta(days=days)
    elif decision == "close":
        review_round.status = ReviewRound.STATUS_CLOSED
        review_round.outcome = ReviewRound.OUTCOME_FAILED
    else:
        raise ValidationError("有不通过的条目，请选择“要求整改”或“结束评审”。")
    review_round.finalized_by = actor
    review_round.finalized_at = now
    review_round.feedback_summary = summary
    review_round.save(
        update_fields=["status", "outcome", "rectify_due_at", "finalized_by", "finalized_at", "feedback_summary"]
    )
    sync_level_reviews(assessment, actor)
    achieved = get_achieved_level(assessment)
    _log(
        actor,
        "review.finalize",
        "ReviewRound",
        review_round.id,
        f"{sys.abbr} 第 {review_round.number} 轮",
        project,
        after={"status": review_round.status, "fail": len(fails), "achieved": achieved},
    )
    if review_round.status == ReviewRound.STATUS_RECTIFYING:
        due = timezone.localtime(review_round.rectify_due_at)
        notify(
            project.owner,
            "review.rectify",
            f"{sys.name}专家评审第 {review_round.number} 轮终审：{len(fails)} 条需整改，请于 {due:%Y-%m-%d} 前提交",
            body=(f"终审意见：{summary}\n" if summary else "") + "请在评价页面查看每条的整改要求，修改后点击“提交整改”。",
            link=enterprise_link(assessment),
            project=project,
            category=CATEGORY_TODO,
            email=True,
        )
    else:
        fill_third_team(project, review_round)
        result_text = f"达成 {sys.abbr} {achieved}" if achieved else "第 1 级未通过，未达成等级"
        notify(
            project.owner,
            "review.close",
            f"{sys.name}专家评审结束：{result_text}",
            body=(f"终审意见：{summary}\n" if summary else "") + "评价机构将据此出具评价报告。",
            link=enterprise_link(assessment),
            project=project,
        )
        experts = {a.expert for a in ExpertAssignment.objects.filter(round__assessment=assessment, status=ExpertAssignment.STATUS_SUBMITTED)}
        notify(
            list(experts),
            "review.closed_for_expert",
            f"「{project.name}」{sys.name}评审已结束，感谢参与",
            project=project,
            category=CATEGORY_TASK,
        )
    return review_round


@transaction.atomic
def extend_rectification(review_round, actor, days):
    _ensure_admin(actor)
    if review_round.status != ReviewRound.STATUS_RECTIFYING:
        raise ValidationError("只有整改中的轮次可以延长整改期限。")
    days = _parse_days(days, 7, 60, "延长天数")
    project = review_round.assessment.project
    before = review_round.rectify_due_at
    review_round.rectify_due_at = max(review_round.rectify_due_at, timezone.now()) + timedelta(days=days)
    review_round.save(update_fields=["rectify_due_at"])
    notify(
        project.owner,
        "review.rectify_extend",
        f"整改期限已延长至 {timezone.localtime(review_round.rectify_due_at):%Y-%m-%d}",
        body=f"项目：{project.name}",
        link=enterprise_link(review_round.assessment),
        project=project,
    )
    _log(
        actor,
        "review.rectify_extend",
        "ReviewRound",
        review_round.id,
        f"第 {review_round.number} 轮",
        project,
        before={"due": before.isoformat()},
        after={"due": review_round.rectify_due_at.isoformat()},
    )
    return review_round


# ---------------------------------------------------------------------------
# 整改（企业）
# ---------------------------------------------------------------------------


@transaction.atomic
def resubmit_rectification(review_round, actor):
    assessment = review_round.assessment
    project = assessment.project
    if not is_project_owner(actor, project):
        raise PermissionDenied("只有申报企业可以提交整改。")
    _ensure_project_alive(project)
    if review_round.status != ReviewRound.STATUS_RECTIFYING:
        raise ValidationError("当前没有需要提交的整改。")
    sys = assessment.sys
    fails = list(review_round.items.filter(final_result=FAIL))
    now = timezone.now()
    review_round.status = ReviewRound.STATUS_CLOSED
    review_round.outcome = ReviewRound.OUTCOME_RECTIFIED
    review_round.rectified_at = now
    review_round.save(update_fields=["status", "outcome", "rectified_at"])
    config = SystemConfig.get()
    new_round = ReviewRound.objects.create(
        assessment=assessment,
        number=review_round.number + 1,
        submitted_by=actor,
        submitted_at=now,
        review_days=config.expert_review_days,
    )
    _snapshot(new_round, CheckItem.objects.filter(id__in=[ri.check_item_id for ri in fails]))
    due = now + timedelta(days=new_round.review_days)
    for previous in review_round.assignments.filter(status=ExpertAssignment.STATUS_SUBMITTED).select_related(
        "expert__expert_profile"
    ):
        expert = previous.expert
        if not (expert.is_active and expert.expert_profile.in_service):
            continue
        # 同一项目已签过承诺，复评直接进入评审
        assignment = ExpertAssignment.objects.create(
            round=new_round,
            expert=expert,
            slot=previous.slot,
            status=ExpertAssignment.STATUS_ACCEPTED,
            assigned_by=None,
            assigned_at=now,
            due_at=due,
            responded_at=now,
            pledge_version=previous.pledge_version,
            pledged_at=previous.pledged_at,
        )
        notify(
            expert,
            "review.rereview",
            f"复评任务：{project.name}（{_round_label(new_round)}，{len(fails)} 条）",
            body=f"企业已按上一轮终审意见完成整改，本轮只复评上一轮未通过的条目。截止时间：{timezone.localtime(due):%Y-%m-%d}",
            link=task_link(assignment),
            project=project,
            category=CATEGORY_TASK,
            email=True,
        )
    status = refresh_round_status(new_round)
    _log(
        actor,
        "review.resubmit",
        "ReviewRound",
        new_round.id,
        f"{sys.abbr} 第 {new_round.number} 轮",
        project,
        after={"items": len(fails)},
    )
    missing = len(free_slots(new_round))
    notify_admins(
        "review.resubmit",
        f"{project.applicant}已提交第 {review_round.number} 轮整改，进入第 {new_round.number} 轮复评",
        body=f"项目：{project.name}\n复评条目：{len(fails)} 条"
        + (f"\n原专家中有 {missing} 位已停用或不在职，需要重新分配。" if status == ReviewRound.STATUS_ASSIGNING else ""),
        link=round_link(new_round),
        project=project,
        category=CATEGORY_TODO if status == ReviewRound.STATUS_ASSIGNING else CATEGORY_RESULT,
    )
    return new_round


# ---------------------------------------------------------------------------
# 项目删除时撤销未完成的评审任务
# ---------------------------------------------------------------------------


def on_project_deleted(project, actor):
    now = timezone.now()
    pending = ExpertAssignment.objects.filter(
        round__assessment__project=project, status__in=ExpertAssignment.PENDING_STATUSES
    ).select_related("round")
    rounds = set()
    for assignment in pending:
        assignment.status = ExpertAssignment.STATUS_CANCELLED
        assignment.cancelled_at = now
        assignment.cancel_reason = "项目已被评价机构删除"
        assignment.save(update_fields=["status", "cancelled_at", "cancel_reason"])
        rounds.add(assignment.round)
        notify(
            assignment.expert,
            "review.revoke",
            f"评审任务已撤销：{project.name}",
            body="该项目已被评价机构删除，你无需继续评审。",
            category=CATEGORY_TASK,
        )
    for review_round in rounds:
        refresh_round_status(review_round)


# ---------------------------------------------------------------------------
# 专家任务访问
# ---------------------------------------------------------------------------


def expert_can_download(assignment, evidence_id):
    """专家只能下载本轮留档中引用的佐证（即使企业之后删除了该文件的引用）。"""
    evidence_id = str(evidence_id)
    return any(
        entry.get("id") == evidence_id
        for evidence in assignment.round.items.values_list("snap_evidence", flat=True)
        for entry in (evidence or [])
    )


# ---------------------------------------------------------------------------
# 评价页上的“专家评审”面板
# ---------------------------------------------------------------------------


def panel_context(assessment, user, admin, owner):
    """评价页面板数据。企业看到的只有轮次状态、期限和终审结论，不含专家身份和专家原话。"""
    review_round = latest_round(assessment)
    context = {
        "mode": assessment.review_mode,
        "is_expert_mode": assessment.is_expert_mode,
        "can_switch": (owner or admin) and can_switch_mode(assessment),
        "round": review_round,
        "rounds_count": assessment.review_rounds.count(),
        "progress": fill_progress(assessment),
        "can_apply": owner and assessment.is_expert_mode and review_round is None,
        "can_resubmit": owner and review_round is not None and review_round.status == ReviewRound.STATUS_RECTIFYING,
        "slots_filled": 0,
        "submitted_count": 0,
        "fails": [],
        "assignments": [],
    }
    if review_round is None:
        return context
    active = active_assignments(review_round)
    context["slots_filled"] = len(active)
    context["submitted_count"] = sum(1 for a in active if a.status == ExpertAssignment.STATUS_SUBMITTED)
    if admin:
        context["assignments"] = active
    if review_round.finalized_at:
        context["fails"] = list(
            review_round.items.filter(final_result=FAIL).select_related("check_item").order_by("check_item__order_index")
        )
    return context


def item_feedback_map(assessment):
    """条目上显示的终审结论：整改中显示整改要求，结束后显示未通过条目的终审意见。{check_item_id: RoundItem}"""
    review_round = latest_round(assessment)
    if review_round is None or not review_round.finalized_at:
        return {}
    return {
        ri.check_item_id: ri for ri in review_round.items.filter(final_result=FAIL)
    }
