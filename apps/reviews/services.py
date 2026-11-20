"""专家评审：管理员对企业已提交的级别，选择自行审核或指派 3 位专家逐条评审。

- 企业照常填写到目标等级并提交审核，不选择评审方式；
- 管理员指派专家评审时，范围是已提交级别中尚未通过专家评审的条目，复评因此只针对上一轮未通过的条目；
- 专家之间互不可见；企业只看管理员终审后的结论和整改要求，看不到专家姓名和专家原话（单盲）；
- 意见不一致或管理员推翻一致意见时，必须写裁定理由；判不通过必须写整改要求；
- 终审后，全部通过的连续级别审核通过，有不通过条目的级别退回企业整改，企业修改后照常提交审核；
- 专家的操作留痕记在评审分配 / 终审条目上，不进入条目修改记录，避免向企业暴露专家身份。
"""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SystemConfig
from apps.accounts.permissions import can_review
from apps.audit.models import AuditLog
from apps.evaluations.models import Assessment, CheckItem, EvaluationTeamMember, LevelReview
from apps.evaluations.services import format_levels
from apps.experts.models import ExpertProfile
from apps.notifications.models import CATEGORY_TASK, CATEGORY_TODO
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

# 专家评审进行中的级别在等级阶梯上的状态
STATE_EXPERT_REVIEW = "expert_review"


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


# ---------------------------------------------------------------------------
# 与评价流程的衔接（evaluations.services 调用）
# ---------------------------------------------------------------------------


def open_round(assessment):
    """进行中的轮次（待分配专家、专家评审中、待终审）；整改中不算，此时企业可以修改。"""
    return assessment.review_rounds.filter(status__in=ReviewRound.LOCKED_STATUSES).first()


def rectifying_round(assessment):
    return assessment.review_rounds.filter(status=ReviewRound.STATUS_RECTIFYING).first()


def expert_locked_item_ids(assessment):
    """整改时冻结的条目：因专家评审被退回的级别里，已通过专家评审的条目。"""
    returned = set(
        assessment.level_reviews.filter(
            status=LevelReview.STATUS_RETURNED, source=LevelReview.SOURCE_EXPERT
        ).values_list("level", flat=True)
    )
    if not returned:
        return set()
    results = latest_results(assessment)
    return {
        item_id
        for item_id in assessment.items.filter(level__in=returned).values_list("id", flat=True)
        if item_id in results and results[item_id].final_result == PASS
    }


def item_lock_reason(item):
    if item.id in expert_locked_item_ids(item.assessment):
        return "该条目已通过专家评审，整改时只修改未通过的条目。"
    return None


def level_overrides(assessment):
    """专家评审进行中的级别，在等级阶梯上显示“专家评审中”。"""
    review_round = open_round(assessment)
    if review_round is None:
        return {}
    return {level: STATE_EXPERT_REVIEW for level in review_round.items.values_list("check_item__level", flat=True)}


def round_step(assessment, for_admin):
    """项目列表“下一步”：专家评审进行中或整改中时的提示；没有这类轮次时返回 None。"""
    review_round = open_round(assessment) or rectifying_round(assessment)
    if review_round is None:
        return None
    n = review_round.number
    if review_round.status == ReviewRound.STATUS_ASSIGNING:
        if for_admin:
            return f"第 {n} 轮专家评审：待分配专家（还差 {len(free_slots(review_round))} 位）", "warning"
        return "评价机构正在安排专家评审", "info"
    if review_round.status == ReviewRound.STATUS_REVIEWING:
        if for_admin:
            active = active_assignments(review_round)
            done = sum(1 for a in active if a.status == ExpertAssignment.STATUS_SUBMITTED)
            overdue = sum(1 for a in active if a.is_overdue)
            text = f"第 {n} 轮专家评审中（已提交 {done}/3）"
            return (text + f"，{overdue} 位逾期", "warning") if overdue else (text, "info")
        return "专家评审中", "info"
    if review_round.status == ReviewRound.STATUS_RULING:
        return (f"第 {n} 轮专家意见已齐，待终审", "warning") if for_admin else ("专家评审中", "info")
    due = timezone.localtime(review_round.rectify_due_at)
    count = review_round.items.filter(final_result=FAIL).count()
    if for_admin:
        late = review_round.rectify_overdue
        return f"专家评审后整改中（{count} 条），截止 {due:%m-%d}" + ("（已逾期）" if late else ""), "warning" if late else "neutral"
    return f"需整改 {count} 条，请于 {due:%m-%d} 前修改后重新提交审核", "warning"


# ---------------------------------------------------------------------------
# 指派专家评审（管理员对已提交的级别）
# ---------------------------------------------------------------------------


def submitted_levels(assessment):
    return sorted(
        assessment.level_reviews.filter(status=LevelReview.STATUS_SUBMITTED).values_list("level", flat=True)
    )


def expert_scope(assessment):
    """本次专家评审的条目：已提交级别中，尚未通过专家评审的条目。"""
    levels = submitted_levels(assessment)
    if not levels:
        return assessment.items.none()
    results = latest_results(assessment)
    passed = [item_id for item_id, ri in results.items() if ri.final_result == PASS]
    return assessment.items.filter(level__in=levels).exclude(id__in=passed)


def previous_experts(assessment):
    """上一轮已提交意见、目前仍可分配的专家（复评时可沿用）。"""
    last = assessment.review_rounds.exclude(status__in=ReviewRound.OPEN_STATUSES).order_by("-number").first()
    if last is None:
        return []
    return [
        a
        for a in last.assignments.filter(status=ExpertAssignment.STATUS_SUBMITTED)
        .select_related("expert__expert_profile")
        .order_by("slot")
        if a.expert.is_active and a.expert.expert_profile.in_service
    ]


def start_blockers(assessment):
    if assessment.status != Assessment.STATUS_IN_PROGRESS:
        return ["该评价已出具报告"]
    if open_round(assessment):
        return ["专家评审正在进行"]
    if rectifying_round(assessment):
        return ["上一轮专家评审的整改尚未全部提交"]
    if not submitted_levels(assessment):
        return ["企业尚未提交待审核的级别"]
    if not expert_scope(assessment).exists():
        return ["已提交级别的条目都已通过专家评审，可直接审核通过"]
    return []


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


def _notify_group_formed(review_round):
    """专家组齐全（3 人都在评审中）时通知企业一次，不透露专家身份。"""
    assessment = review_round.assessment
    project = assessment.project
    if remind_once(f"group_formed:{review_round.id}", "review.group_formed"):
        levels = format_levels(sorted(set(review_round.items.values_list("check_item__level", flat=True))))
        notify(
            project.owner,
            "review.group_formed",
            f"评价机构已安排专家评审{assessment.sys.name}第 {levels} 级（3 位专家）",
            body=f"项目：{project.name}\n评审期限 {review_round.review_days} 天，评审期间这些级别冻结，结果出来后会通知你。",
            link=enterprise_link(assessment),
            project=project,
        )


@transaction.atomic
def start_expert_review(assessment, actor, reuse_experts=False, review_days=None):
    """管理员把企业已提交的级别交给专家评审。reuse_experts=True 时沿用上一轮专家组（已签承诺，直接进入评审）。"""
    _ensure_admin(actor)
    project = assessment.project
    _ensure_project_alive(project)
    blockers = start_blockers(assessment)
    if blockers:
        raise ValidationError(f"现在不能指派专家评审：{'；'.join(blockers)}。")
    scope = list(expert_scope(assessment))
    days = _parse_days(review_days, SystemConfig.get().expert_review_days, 60, "专家评审期限")
    latest_submit = (
        assessment.level_reviews.filter(status=LevelReview.STATUS_SUBMITTED).order_by("-submitted_at").first()
    )
    number = (assessment.review_rounds.aggregate(n=Max("number"))["n"] or 0) + 1
    review_round = ReviewRound.objects.create(
        assessment=assessment,
        number=number,
        submitted_by=latest_submit.submitted_by,
        submitted_at=latest_submit.submitted_at or timezone.now(),
        review_days=days,
    )
    _snapshot(review_round, CheckItem.objects.filter(id__in=[item.id for item in scope]))
    levels = sorted({item.level for item in scope})
    _log(
        actor,
        "review.start",
        "ReviewRound",
        review_round.id,
        f"{assessment.sys.abbr} 第 {number} 轮",
        project,
        after={"levels": levels, "items": len(scope), "reuse": bool(reuse_experts)},
    )
    if reuse_experts:
        now = timezone.now()
        due = now + timedelta(days=days)
        for previous in previous_experts(assessment):
            assignment = ExpertAssignment.objects.create(
                round=review_round,
                expert=previous.expert,
                slot=previous.slot,
                status=ExpertAssignment.STATUS_ACCEPTED,
                assigned_by=actor,
                assigned_at=now,
                due_at=due,
                responded_at=now,
                pledge_version=previous.pledge_version,
                pledged_at=previous.pledged_at,
            )
            notify(
                previous.expert,
                "review.rereview",
                f"复评任务：{project.name}（{_round_label(review_round)}，{len(scope)} 条）",
                body=f"企业已按上一轮终审意见完成整改，本轮只复评上一轮未通过的条目。截止时间：{timezone.localtime(due):%Y-%m-%d}",
                link=task_link(assignment),
                project=project,
                category=CATEGORY_TASK,
                email=True,
            )
    if refresh_round_status(review_round) == ReviewRound.STATUS_REVIEWING:
        _notify_group_formed(review_round)
    return review_round


def on_levels_submitted(assessment, actor):
    """企业重新提交后，若上一轮专家评审退回的级别都已重新提交，整改轮次结束，等待管理员决定复评方式。"""
    review_round = rectifying_round(assessment)
    if review_round is None:
        return None
    failed_levels = set(review_round.items.filter(final_result=FAIL).values_list("check_item__level", flat=True))
    resubmitted = set(submitted_levels(assessment))
    if not failed_levels <= resubmitted:
        return None
    review_round.status = ReviewRound.STATUS_CLOSED
    review_round.outcome = ReviewRound.OUTCOME_RECTIFIED
    review_round.rectified_at = timezone.now()
    review_round.save(update_fields=["status", "outcome", "rectified_at"])
    _log(
        actor,
        "review.resubmit",
        "ReviewRound",
        review_round.id,
        f"{assessment.sys.abbr} 第 {review_round.number} 轮",
        assessment.project,
        after={"levels": sorted(failed_levels)},
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
    if refresh_round_status(review_round) == ReviewRound.STATUS_REVIEWING:
        _notify_group_formed(review_round)
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
    if refresh_round_status(review_round) == ReviewRound.STATUS_REVIEWING:
        _notify_group_formed(review_round)
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
    """提交前检查：未给结论的条目、判不通过但没写满足情况评价或支撑材料评价的条目。"""
    opinions = opinions_by_item(assignment)
    missing, unexplained = [], []
    for round_item in task_items(assignment):
        opinion = opinions.get(round_item.id)
        if not opinion or not opinion.verdict:
            missing.append(round_item)
        elif opinion.verdict == FAIL and not (opinion.comment or opinion.suggestion):  # 支撑材料评价 / 满足情况评价
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
        raise ValidationError(f"判为不通过的条目须填写满足情况评价或支撑材料评价：{codes}。")
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


def _apply_ruling_to_levels(review_round, actor, summary):
    """把终审结果落到逐级审核记录：返回 (通过的级别, 退回的级别)。

    全部条目通过专家评审的级别，自第一个待审级别起连续的审核通过；
    有不通过条目的级别退回企业；其后全部通过但不连续的级别保持“已提交”，待前面的级别整改通过后再通过。
    """
    from apps.evaluations.services import get_pass_plan

    assessment = review_round.assessment
    results = latest_results(assessment)
    by_level = {}
    for item_id, level in assessment.items.values_list("id", "level"):
        by_level.setdefault(level, []).append(item_id)
    round_levels = set(review_round.items.values_list("check_item__level", flat=True))
    failed = sorted(
        level
        for level in round_levels
        if any(i in results and results[i].final_result == FAIL for i in by_level.get(level, []))
    )
    passed = []
    for level in get_pass_plan(assessment):
        ids = by_level.get(level, [])
        if not ids or not all(i in results and results[i].final_result == PASS for i in ids):
            break
        passed.append(level)
    now = timezone.now()
    if passed:
        LevelReview.objects.filter(assessment=assessment, level__in=passed).update(
            status=LevelReview.STATUS_PASSED,
            source=LevelReview.SOURCE_EXPERT,
            reviewed_by=actor,
            reviewed_at=now,
            comment="",
        )
    if failed:
        comment = summary or f"第 {review_round.number} 轮专家评审有条目未通过，请按条目上的整改要求修改后重新提交。"
        LevelReview.objects.filter(assessment=assessment, level__in=failed).update(
            status=LevelReview.STATUS_RETURNED,
            source=LevelReview.SOURCE_EXPERT,
            reviewed_by=actor,
            reviewed_at=now,
            comment=comment,
        )
    assessment.save(update_fields=["updated_at"])
    return passed, failed


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
def finalize_round(review_round, actor, rectify_days=None, summary=""):
    """提交终审结果。有不通过条目时，对应级别退回企业并设整改期限；全部通过的连续级别审核通过。"""
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
    if fails:
        days = _parse_days(rectify_days, SystemConfig.get().rectify_days, 90, "整改期限")
        review_round.status = ReviewRound.STATUS_RECTIFYING
        review_round.rectify_due_at = now + timedelta(days=days)
    else:
        review_round.status = ReviewRound.STATUS_CLOSED
        review_round.outcome = ReviewRound.OUTCOME_PASSED
    review_round.finalized_by = actor
    review_round.finalized_at = now
    review_round.feedback_summary = summary
    review_round.save(
        update_fields=["status", "outcome", "rectify_due_at", "finalized_by", "finalized_at", "feedback_summary"]
    )
    passed, failed = _apply_ruling_to_levels(review_round, actor, summary)
    achieved = get_achieved_level(assessment)
    _log(
        actor,
        "review.finalize",
        "ReviewRound",
        review_round.id,
        f"{sys.abbr} 第 {review_round.number} 轮",
        project,
        after={"status": review_round.status, "fail": len(fails), "passed_levels": passed, "returned_levels": failed, "achieved": achieved},
    )
    if passed:
        notify(
            project.owner,
            "level.pass",
            f"{sys.abbr} 第 {format_levels(passed)} 级已通过专家评审",
            body=f"项目：{project.name}" + (f"\n已达到目标等级 {sys.abbr} {assessment.target_level}。" if achieved >= assessment.target_level else ""),
            link=enterprise_link(assessment),
            project=project,
        )
    if fails:
        due = timezone.localtime(review_round.rectify_due_at)
        notify(
            project.owner,
            "review.rectify",
            f"{sys.name}专家评审第 {review_round.number} 轮终审：第 {format_levels(failed)} 级有 {len(fails)} 条需整改，请于 {due:%Y-%m-%d} 前重新提交",
            body=(f"终审意见：{summary}\n" if summary else "") + "请在评价页面查看每条的整改要求，修改后照常“提交审核”。",
            link=enterprise_link(assessment),
            project=project,
            category=CATEGORY_TODO,
            email=True,
        )
    else:
        # 专家组署名只在没有待整改条目时写入报告评价组，评审进行中不向企业暴露专家身份
        fill_third_team(project, review_round)
        experts = [a.expert for a in review_round.assignments.filter(status=ExpertAssignment.STATUS_SUBMITTED)]
        notify(
            experts,
            "review.closed_for_expert",
            f"「{project.name}」{_round_label(review_round)}评审已结束，感谢参与",
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
    blockers = start_blockers(assessment) if admin else []
    context = {
        "round": review_round,
        "rounds_count": assessment.review_rounds.count(),
        "slots_filled": 0,
        "submitted_count": 0,
        "fails": [],
        "assignments": [],
        "can_start": admin and not blockers,
        "start_blockers": blockers,
        "scope_count": expert_scope(assessment).count() if admin and not blockers else 0,
        "previous_experts": previous_experts(assessment) if admin and not blockers else [],
        "review_days": SystemConfig.get().expert_review_days,
    }
    if review_round is None:
        return context
    active = active_assignments(review_round)
    context["slots_filled"] = len(active)
    context["submitted_count"] = sum(1 for a in active if a.status == ExpertAssignment.STATUS_SUBMITTED)
    if admin:
        context["assignments"] = active
    if review_round.status == ReviewRound.STATUS_RECTIFYING:
        context["fails"] = list(
            review_round.items.filter(final_result=FAIL).select_related("check_item").order_by("check_item__order_index")
        )
    return context


def item_feedback_map(assessment):
    """整改中的条目上显示终审的整改要求。{check_item_id: RoundItem}"""
    review_round = rectifying_round(assessment)
    if review_round is None:
        return {}
    return {ri.check_item_id: ri for ri in review_round.items.filter(final_result=FAIL)}
