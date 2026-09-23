"""评价流程服务：企业逐条自评 → 提交审核（逐级或一次到目标级）→ 管理员审核通过或退回。

系统只做流程门禁与留痕，不做任何自动达标判断：是否通过由管理员人工决定。
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts.permissions import can_review, is_project_owner
from apps.audit.models import AuditLog
from apps.evaluations.models import Assessment, CheckItem, EvaluationProject, LevelReview
from apps.rules.models import RuleItem
from apps.rules.systems import TRL, get_system

# 技术类型 → 适用的技术就绪度细则类别（制造成熟度细则不区分技术类型）
TECH_TYPE_TRACKS = {
    EvaluationProject.TECH_EQUIPMENT: {RuleItem.TRACK_HARDWARE, RuleItem.TRACK_SOFTWARE},
    EvaluationProject.TECH_HARDWARE: {RuleItem.TRACK_HARDWARE},
    EvaluationProject.TECH_SOFTWARE: {RuleItem.TRACK_SOFTWARE},
}

# 阶梯上每一级的状态
STATE_DRAFT = "draft"  # 填写中
STATE_READY = "ready"  # 已填完、待提交
STATE_SUBMITTED = LevelReview.STATUS_SUBMITTED
STATE_RETURNED = LevelReview.STATUS_RETURNED
STATE_PASSED = LevelReview.STATUS_PASSED
STATE_OUT_OF_SCOPE = "out_of_scope"

STATE_LABELS = {
    STATE_DRAFT: "填写中",
    STATE_READY: "待提交",
    STATE_SUBMITTED: "已提交",
    STATE_RETURNED: "已退回",
    STATE_PASSED: "已通过",
    STATE_OUT_OF_SCOPE: "超出目标",
}


def format_levels(levels):
    """[1,2,3] → “1~3”；[2,4] → “2、4”。"""
    levels = sorted(levels)
    if not levels:
        return ""
    if len(levels) > 1 and levels == list(range(levels[0], levels[-1] + 1)):
        return f"{levels[0]}~{levels[-1]}"
    return "、".join(str(level) for level in levels)


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


def rules_available(system):
    return RuleItem.objects.filter(system=system).exists()


# ---------------------------------------------------------------------------
# 项目与评价的创建
# ---------------------------------------------------------------------------


@transaction.atomic
def create_project(project, owner, targets):
    """保存企业申报的项目，并按所选评价类别生成评价条目。targets: {system: target_level}"""
    if not targets:
        raise ValidationError("请至少选择一项评价内容（技术就绪度或制造成熟度）。")
    project.owner = owner
    project.save()
    _log(owner, "project.create", "EvaluationProject", project.id, project.name, project, after={"name": project.name})
    for system, target_level in targets.items():
        add_assessment(project, system, target_level, owner)
    return project


@transaction.atomic
def add_assessment(project, system, target_level, actor):
    sys = get_system(system)
    target_level = int(target_level)
    if not 1 <= target_level <= sys.max_level:
        raise ValidationError(f"目标等级必须是 {sys.abbr} 1~{sys.max_level}。")
    if project.assessments.filter(system=system).exists():
        raise ValidationError(f"该项目已包含{sys.name}评价。")
    assessment = Assessment.objects.create(project=project, system=system, target_level=target_level)
    count = generate_check_items(assessment)
    _log(
        actor,
        "assessment.create",
        "Assessment",
        assessment.id,
        f"{sys.name}评价",
        project,
        after={"system": system, "target_level": target_level, "items": count},
    )
    return assessment


def generate_check_items(assessment):
    """按评价类别（及技术类型）从细则库复制全部等级的条目。"""
    if assessment.items.exists():
        raise ValidationError("评价条目已经生成。")
    sys = assessment.sys
    rules = RuleItem.objects.filter(system=assessment.system)
    if assessment.system == TRL:
        rules = rules.filter(track__in=TECH_TYPE_TRACKS[assessment.project.tech_type])
    rules = list(rules.order_by("level", "track", "seq"))
    if not rules:
        raise ValidationError(f"{sys.name}评价细则尚未导入，暂不能开展{sys.name}评价。")
    CheckItem.objects.bulk_create(
        CheckItem(
            assessment=assessment,
            order_index=index,
            level=rule.level,
            track=rule.track,
            seq=rule.seq,
            name=rule.name,
            evidence=rule.evidence,
        )
        for index, rule in enumerate(rules, start=1)
    )
    return len(rules)


# ---------------------------------------------------------------------------
# 状态计算
# ---------------------------------------------------------------------------


def get_reviews(assessment):
    return {review.level: review for review in assessment.level_reviews.select_related("submitted_by", "reviewed_by")}


def get_achieved_level(assessment, reviews=None):
    """达成等级 = 自第 1 级起连续审核通过的最高级别。"""
    reviews = get_reviews(assessment) if reviews is None else reviews
    achieved = 0
    for level in assessment.sys.levels:
        review = reviews.get(level)
        if review and review.status == LevelReview.STATUS_PASSED:
            achieved = level
        else:
            break
    return achieved


def _level_counts(assessment):
    rows = (
        assessment.items.values("level")
        .annotate(
            total=Count("id"),
            unevaluated=Count("id", filter=Q(result=CheckItem.RESULT_UNEVALUATED)),
            satisfied=Count("id", filter=Q(result=CheckItem.RESULT_SATISFIED)),
            not_satisfied=Count("id", filter=Q(result=CheckItem.RESULT_NOT_SATISFIED)),
            not_applicable=Count("id", filter=Q(result=CheckItem.RESULT_NOT_APPLICABLE)),
        )
        .order_by("level")
    )
    return {row["level"]: row for row in rows}


def _review_state(assessment, level, reviews):
    if level > assessment.target_level:
        return STATE_OUT_OF_SCOPE
    review = reviews.get(level)
    return review.status if review else None


def is_level_editable(assessment, level, reviews=None):
    """企业可编辑：评价进行中、在目标等级内、且该级未提交 / 未通过（被退回的可以改）。"""
    if assessment.status != Assessment.STATUS_IN_PROGRESS:
        return False
    reviews = get_reviews(assessment) if reviews is None else reviews
    return _review_state(assessment, level, reviews) in (None, STATE_RETURNED)


def get_level_states(assessment, reviews=None, counts=None):
    reviews = get_reviews(assessment) if reviews is None else reviews
    counts = _level_counts(assessment) if counts is None else counts
    empty = {"total": 0, "unevaluated": 0, "satisfied": 0, "not_satisfied": 0, "not_applicable": 0}
    states = []
    for level in assessment.sys.levels:
        stats = counts.get(level, empty)
        state = _review_state(assessment, level, reviews)
        if state is None:
            state = STATE_READY if stats["total"] and not stats["unevaluated"] else STATE_DRAFT
        completed = stats["total"] - stats["unevaluated"]
        states.append(
            {
                "level": level,
                "state": state,
                "label": STATE_LABELS[state],
                "review": reviews.get(level),
                "is_target": level == assessment.target_level,
                "total": stats["total"],
                "completed": completed,
                "unevaluated": stats["unevaluated"],
                "satisfied": stats["satisfied"],
                "not_satisfied": stats["not_satisfied"],
                "not_applicable": stats["not_applicable"],
            }
        )
    return states


def get_scope_stats(assessment):
    """目标等级以内条目的填写进度（列表进度条与 KPI 使用）。"""
    counts = assessment.items.filter(level__lte=assessment.target_level).aggregate(
        total=Count("id"),
        unevaluated=Count("id", filter=Q(result=CheckItem.RESULT_UNEVALUATED)),
        satisfied=Count("id", filter=Q(result=CheckItem.RESULT_SATISFIED)),
        not_satisfied=Count("id", filter=Q(result=CheckItem.RESULT_NOT_SATISFIED)),
        not_applicable=Count("id", filter=Q(result=CheckItem.RESULT_NOT_APPLICABLE)),
    )
    counts["completed"] = counts["total"] - counts["unevaluated"]
    counts["percent"] = round(counts["completed"] * 100 / counts["total"]) if counts["total"] else 0
    return counts


def get_submit_plan(assessment, reviews=None, counts=None):
    """企业可选的提交范围：从最低的未提交级起，可提交至目标等级内任一未提交级。

    每个选项：up_to（提交至第几级）、levels（本次将提交的级别）、ready、missing[(level, 未填条数)]。
    """
    if assessment.status != Assessment.STATUS_IN_PROGRESS:
        return []
    reviews = get_reviews(assessment) if reviews is None else reviews
    counts = _level_counts(assessment) if counts is None else counts
    open_levels = [
        level
        for level in range(1, assessment.target_level + 1)
        if _review_state(assessment, level, reviews) in (None, STATE_RETURNED)
    ]
    options = []
    for up_to in open_levels:
        levels = [level for level in open_levels if level <= up_to]
        missing = [
            (level, counts.get(level, {}).get("unevaluated", 0))
            for level in levels
            if counts.get(level, {}).get("unevaluated", 0)
        ]
        options.append({"up_to": up_to, "levels": levels, "levels_text": format_levels(levels), "ready": not missing, "missing": missing})
    return options


def get_pass_plan(assessment, reviews=None):
    """管理员可审核通过的级别：必须从“已达成等级 + 1”起、连续且已提交。"""
    if assessment.status != Assessment.STATUS_IN_PROGRESS:
        return []
    reviews = get_reviews(assessment) if reviews is None else reviews
    level = get_achieved_level(assessment, reviews) + 1
    levels = []
    while level <= assessment.target_level:
        review = reviews.get(level)
        if not review or review.status != LevelReview.STATUS_SUBMITTED:
            break
        levels.append(level)
        level += 1
    return levels


def get_submitted_levels(assessment, reviews=None):
    reviews = get_reviews(assessment) if reviews is None else reviews
    return sorted(level for level, review in reviews.items() if review.status == LevelReview.STATUS_SUBMITTED)


def get_returnable_levels(assessment, reviews=None):
    """管理员可退回：待审核的级别，以及（重新开放后需要复核的）已通过级别。"""
    reviews = get_reviews(assessment) if reviews is None else reviews
    return sorted(
        level
        for level, review in reviews.items()
        if review.status in (LevelReview.STATUS_SUBMITTED, LevelReview.STATUS_PASSED)
    )


def get_returned_levels(assessment, reviews=None):
    reviews = get_reviews(assessment) if reviews is None else reviews
    return sorted(level for level, review in reviews.items() if review.status == LevelReview.STATUS_RETURNED)


def get_attention_items(assessment, levels):
    """提交 / 审核前的提醒（不阻断）：自评满足但未挂佐证、自评不满足的条目。"""
    items = list(
        assessment.items.filter(level__in=levels).prefetch_related("evidence_files").order_by("level", "track", "seq")
    )
    no_evidence = [item for item in items if item.result == CheckItem.RESULT_SATISFIED and not item.active_evidence_count]
    not_satisfied = [item for item in items if item.result == CheckItem.RESULT_NOT_SATISFIED]
    return {"no_evidence": no_evidence, "not_satisfied": not_satisfied}


def next_step(assessment, for_admin):
    """列表“下一步”提示：(文字, 标签色)。"""
    sys = assessment.sys
    if assessment.status == Assessment.STATUS_REPORTED:
        return ("报告已出具" if for_admin else "报告已出具，可下载"), "success"
    reviews = get_reviews(assessment)
    achieved = get_achieved_level(assessment, reviews)
    if achieved >= assessment.target_level:
        return ("已全部通过，可生成报告" if for_admin else "已全部审核通过，等待出具报告"), "success"
    submitted = get_submitted_levels(assessment, reviews)
    returned = get_returned_levels(assessment, reviews)
    if for_admin:
        if submitted:
            return f"待审核：第 {format_levels(submitted)} 级", "warning"
        if returned:
            return f"已退回第 {format_levels(returned)} 级，等待企业修改", "neutral"
        return "企业填写中", "neutral"
    if returned:
        return f"第 {format_levels(returned)} 级被退回，请按审核意见修改后重新提交", "warning"
    plan = get_submit_plan(assessment, reviews)
    if plan:
        first = plan[0]
        if first["missing"]:
            level, count = first["missing"][0]
            return f"请填写第 {level} 级（剩 {count} 条）", "neutral"
        return f"第 {first['levels_text']} 级已填完，可提交审核", "info"
    if submitted:
        return f"已提交第 {format_levels(submitted)} 级，等待审核", "info"
    return f"{sys.name}评价进行中", "neutral"


# ---------------------------------------------------------------------------
# 企业：逐条自评
# ---------------------------------------------------------------------------


def _evaluation_snapshot(item):
    return {"result": item.result, "statement": item.statement, "support_info": item.support_info}


def validate_evaluation_payload(data):
    result = data.get("result")
    statement = (data.get("statement") or "").strip()
    support_info = (data.get("support_info") or "").strip()

    if result not in {CheckItem.RESULT_SATISFIED, CheckItem.RESULT_NOT_SATISFIED, CheckItem.RESULT_NOT_APPLICABLE}:
        raise ValidationError("请选择自评结论：满足、不满足或不适用。")
    if result == CheckItem.RESULT_SATISFIED and not statement:
        raise ValidationError("自评为满足时，必须填写满足情况说明。")
    if result == CheckItem.RESULT_NOT_SATISFIED and not statement:
        raise ValidationError("自评为不满足时，必须填写差距与不满足情况说明。")
    if result == CheckItem.RESULT_NOT_APPLICABLE and not statement:
        raise ValidationError("自评为不适用时，必须填写不适用理由。")
    return {"result": result, "statement": statement, "support_info": support_info}


def ensure_item_editable(item, purpose="修改"):
    assessment = item.assessment
    sys = assessment.sys
    if assessment.status == Assessment.STATUS_REPORTED:
        raise ValidationError(f"{sys.name}评价已出具报告，内容已冻结，不能{purpose}。如需修改请联系评价机构。")
    if item.level > assessment.target_level:
        raise ValidationError(f"第 {item.level} 级超出目标等级 {sys.abbr} {assessment.target_level}，无需填写。")
    state = _review_state(assessment, item.level, get_reviews(assessment))
    if state == STATE_SUBMITTED:
        raise ValidationError(f"第 {item.level} 级已提交审核，等待评价机构审核期间不能{purpose}。")
    if state == STATE_PASSED:
        raise ValidationError(f"第 {item.level} 级已审核通过，内容已冻结，不能{purpose}。")


@transaction.atomic
def evaluate_check_item(item, actor, data):
    if not is_project_owner(actor, item.assessment.project):
        raise PermissionDenied("只有申报企业可以填写自评。")
    ensure_item_editable(item)
    before = _evaluation_snapshot(item)
    payload = validate_evaluation_payload(data)

    item.result = payload["result"]
    item.statement = payload["statement"]
    item.support_info = payload["support_info"]
    item.evaluated_by = actor
    item.evaluated_at = timezone.now()
    item.save(update_fields=["result", "statement", "support_info", "evaluated_by", "evaluated_at", "updated_at"])

    _log(actor, "item.evaluate", "CheckItem", item.id, item.code, item.assessment.project, before, _evaluation_snapshot(item))
    return item


# ---------------------------------------------------------------------------
# 企业：提交审核
# ---------------------------------------------------------------------------


@transaction.atomic
def submit_levels(assessment, actor, up_to):
    if not is_project_owner(actor, assessment.project):
        raise PermissionDenied("只有申报企业可以提交审核。")
    sys = assessment.sys
    if assessment.status != Assessment.STATUS_IN_PROGRESS:
        raise ValidationError(f"{sys.name}评价已出具报告，不能再提交。")
    try:
        up_to = int(up_to)
    except (TypeError, ValueError):
        raise ValidationError("请选择提交范围。")
    option = next((opt for opt in get_submit_plan(assessment) if opt["up_to"] == up_to), None)
    if option is None:
        raise ValidationError("提交范围无效：须从最低的未提交级别起连续提交，且不超过目标等级。")
    if not option["ready"]:
        detail = "；".join(f"第 {level} 级还有 {count} 条未填写" for level, count in option["missing"])
        raise ValidationError(f"还有条目未填写，不能提交：{detail}。")

    now = timezone.now()
    for level in option["levels"]:
        LevelReview.objects.update_or_create(
            assessment=assessment,
            level=level,
            defaults={
                "status": LevelReview.STATUS_SUBMITTED,
                "submitted_by": actor,
                "submitted_at": now,
                "reviewed_by": None,
                "reviewed_at": None,
            },
        )
    assessment.save(update_fields=["updated_at"])
    _log(
        actor,
        "level.submit",
        "Assessment",
        assessment.id,
        f"{sys.abbr} 第 {option['levels_text']} 级",
        assessment.project,
        after={"system": assessment.system, "levels": option["levels"]},
    )
    return option["levels"]


# ---------------------------------------------------------------------------
# 管理员：审核通过 / 退回 / 逐条意见 / 重新开放
# ---------------------------------------------------------------------------


def _ensure_reviewer(actor):
    if not can_review(actor):
        raise PermissionDenied("只有评价机构管理员可以审核。")


@transaction.atomic
def pass_levels(assessment, actor, up_to):
    _ensure_reviewer(actor)
    sys = assessment.sys
    try:
        up_to = int(up_to)
    except (TypeError, ValueError):
        raise ValidationError("请选择审核通过的范围。")
    plan = get_pass_plan(assessment)
    if up_to not in plan:
        raise ValidationError("只能从最低的待审核级别起，按顺序审核通过已提交的级别。")
    levels = [level for level in plan if level <= up_to]
    now = timezone.now()
    LevelReview.objects.filter(assessment=assessment, level__in=levels).update(
        status=LevelReview.STATUS_PASSED, reviewed_by=actor, reviewed_at=now, comment=""
    )
    # 逐条审核意见已处理完毕，通过后清空（历史见审计留痕）
    assessment.items.filter(level__in=levels).exclude(review_comment="").update(review_comment="")
    assessment.save(update_fields=["updated_at"])
    _log(
        actor,
        "level.pass",
        "Assessment",
        assessment.id,
        f"{sys.abbr} 第 {format_levels(levels)} 级",
        assessment.project,
        after={"system": assessment.system, "levels": levels},
    )
    return levels


@transaction.atomic
def return_levels(assessment, actor, levels, comment):
    _ensure_reviewer(actor)
    sys = assessment.sys
    comment = (comment or "").strip()
    if not comment:
        raise ValidationError("退回时必须填写审核意见，告知企业需要修改的内容。")
    try:
        levels = sorted({int(level) for level in levels})
    except (TypeError, ValueError):
        raise ValidationError("退回级别无效。")
    if not levels:
        raise ValidationError("请选择要退回的级别。")
    if assessment.status != Assessment.STATUS_IN_PROGRESS:
        raise ValidationError(f"{sys.name}评价已出具报告，如需退回请先重新开放。")
    returnable = set(get_returnable_levels(assessment))
    invalid = [level for level in levels if level not in returnable]
    if invalid:
        raise ValidationError(f"第 {format_levels(invalid)} 级不是已提交或已通过状态，不能退回。")
    now = timezone.now()
    LevelReview.objects.filter(assessment=assessment, level__in=levels).update(
        status=LevelReview.STATUS_RETURNED, reviewed_by=actor, reviewed_at=now, comment=comment
    )
    assessment.save(update_fields=["updated_at"])
    _log(
        actor,
        "level.return",
        "Assessment",
        assessment.id,
        f"{sys.abbr} 第 {format_levels(levels)} 级",
        assessment.project,
        after={"system": assessment.system, "levels": levels, "comment": comment},
    )
    return levels


@transaction.atomic
def set_review_comment(item, actor, comment):
    _ensure_reviewer(actor)
    assessment = item.assessment
    state = _review_state(assessment, item.level, get_reviews(assessment))
    if assessment.status != Assessment.STATUS_IN_PROGRESS or state == STATE_PASSED:
        raise ValidationError("该级已审核通过或已出具报告，不能再填写审核意见。")
    before = {"review_comment": item.review_comment}
    item.review_comment = (comment or "").strip()
    item.save(update_fields=["review_comment", "updated_at"])
    _log(actor, "item.review", "CheckItem", item.id, item.code, assessment.project, before, {"review_comment": item.review_comment})
    return item


@transaction.atomic
def reopen_assessment(assessment, actor):
    _ensure_reviewer(actor)
    if assessment.status != Assessment.STATUS_REPORTED:
        raise ValidationError("该评价尚未出具报告，无需重新开放。")
    assessment.status = Assessment.STATUS_IN_PROGRESS
    assessment.save(update_fields=["status", "updated_at"])
    _log(
        actor,
        "assessment.reopen",
        "Assessment",
        assessment.id,
        f"{assessment.sys.name}评价",
        assessment.project,
        before={"status": Assessment.STATUS_REPORTED},
        after={"status": assessment.status},
    )
    return assessment
