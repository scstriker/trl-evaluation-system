from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts.permissions import can_lock_or_unlock_project
from apps.audit.models import AuditLog
from apps.evaluations.models import CheckItem, EvaluationProject, LevelGate
from apps.rules.models import RuleItem

# 轨道 → 物化的细则轨别集合
TRACK_RULE_MAP = {
    EvaluationProject.TRACK_EQUIPMENT: {RuleItem.TRACK_HARDWARE, RuleItem.TRACK_SOFTWARE},
    EvaluationProject.TRACK_HARDWARE: {RuleItem.TRACK_HARDWARE},
    EvaluationProject.TRACK_SOFTWARE: {RuleItem.TRACK_SOFTWARE},
}


@transaction.atomic
def generate_check_items(project):
    """项目创建即快照物化：按轨道从规则库克隆全部 1~9 级条目。"""
    if project.items.exists():
        raise ValidationError("评价条目已经生成。")
    tracks = TRACK_RULE_MAP[project.track]
    rules = [rule for rule in RuleItem.objects.all() if rule.track in tracks]
    if not rules:
        raise ValidationError("当前规则库没有可生成条目，请先导入《评价细则》种子数据。")

    items = [
        CheckItem(
            project=project,
            order_index=index,
            level=rule.level,
            track=rule.track,
            seq=rule.seq,
            name=rule.name,
            evidence=rule.evidence,
        )
        for index, rule in enumerate(rules, start=1)
    ]
    CheckItem.objects.bulk_create(items)
    return len(items)


def _evaluation_snapshot(item):
    return {
        "result": item.result,
        "statement": item.statement,
        "support_info": item.support_info,
    }


def validate_evaluation_payload(data):
    result = data.get("result")
    statement = (data.get("statement") or "").strip()
    support_info = (data.get("support_info") or "").strip()

    if result not in {
        CheckItem.RESULT_SATISFIED,
        CheckItem.RESULT_NOT_SATISFIED,
        CheckItem.RESULT_NOT_APPLICABLE,
    }:
        raise ValidationError("判定结论必须选择满足、不满足或不适用。")
    if result == CheckItem.RESULT_SATISFIED and not statement:
        raise ValidationError("判定为满足时，必须填写满足情况说明。")
    if result == CheckItem.RESULT_NOT_SATISFIED and not statement:
        raise ValidationError("判定为不满足时，必须填写差距与不满足情况说明。")
    if result == CheckItem.RESULT_NOT_APPLICABLE and not statement:
        raise ValidationError("判定为不适用时，必须填写不适用理由。")

    return {"result": result, "statement": statement, "support_info": support_info}


def validate_project_allows_evaluation(project):
    if project.status == EvaluationProject.STATUS_ARCHIVED:
        raise ValidationError("当前项目已归档，不能修改判定结论。")
    if project.status == EvaluationProject.STATUS_LOCKED:
        raise ValidationError("当前项目已锁定，不能修改判定结论。")
    if project.status == EvaluationProject.STATUS_REPORTED:
        raise ValidationError("当前项目已生成报告，不能修改判定结论。请先解锁后复核。")


@transaction.atomic
def evaluate_check_item(item, actor, data):
    validate_project_allows_evaluation(item.project)
    if item.level > item.project.unlocked_level:
        raise ValidationError(f"TRL {item.level} 级尚未解锁，请先完成前序级别的审核。")
    if LevelGate.objects.filter(project=item.project, level=item.level).exists():
        raise ValidationError(f"TRL {item.level} 级已审核通过，判定结论已冻结。")

    before = _evaluation_snapshot(item)
    payload = validate_evaluation_payload(data)

    item.result = payload["result"]
    item.statement = payload["statement"]
    item.support_info = payload["support_info"]
    item.evaluated_by = actor
    item.evaluated_at = timezone.now()
    item.save(update_fields=["result", "statement", "support_info", "evaluated_by", "evaluated_at", "updated_at"])

    AuditLog.objects.create(
        actor=actor,
        action="item.evaluate",
        model_name="CheckItem",
        object_id=str(item.id),
        object_repr=item.code,
        project=item.project,
        before=before,
        after=_evaluation_snapshot(item),
    )
    return item


@transaction.atomic
def pass_level(project, level, actor):
    """审核通过本级：仅做“全部条目已判定”的流程门禁，不做达标算分判断。"""
    validate_project_allows_evaluation(project)
    if level != project.unlocked_level:
        raise ValidationError("只能对当前核验级别执行审核确认。")
    if LevelGate.objects.filter(project=project, level=level).exists():
        raise ValidationError(f"TRL {level} 级已审核通过，不能重复操作。")

    level_items = project.items.filter(level=level)
    unevaluated = level_items.filter(result=CheckItem.RESULT_UNEVALUATED).count()
    if unevaluated:
        raise ValidationError(f"当前级别还有 {unevaluated} 条细则未判定，全部判定后方可审核通过。")

    gate = LevelGate.objects.create(project=project, level=level, passed_by=actor)
    if project.unlocked_level < 9:
        project.unlocked_level = level + 1
        project.save(update_fields=["unlocked_level", "updated_at"])

    AuditLog.objects.create(
        actor=actor,
        action="level.pass",
        model_name="LevelGate",
        object_id=str(gate.id),
        object_repr=f"TRL {level}",
        project=project,
        before={"unlocked_level": level},
        after={"unlocked_level": project.unlocked_level, "passed_level": level},
    )
    return gate


def get_achieved_level(project):
    """达成等级 = 从 1 级起连续审核通过的最高级别。"""
    passed = set(project.level_gates.values_list("level", flat=True))
    achieved = 0
    for level in range(1, 10):
        if level in passed:
            achieved = level
        else:
            break
    return achieved


def get_project_stats(project):
    counts = project.items.aggregate(
        total=Count("id"),
        satisfied=Count("id", filter=Q(result=CheckItem.RESULT_SATISFIED)),
        not_satisfied=Count("id", filter=Q(result=CheckItem.RESULT_NOT_SATISFIED)),
        not_applicable=Count("id", filter=Q(result=CheckItem.RESULT_NOT_APPLICABLE)),
        unevaluated=Count("id", filter=Q(result=CheckItem.RESULT_UNEVALUATED)),
    )
    counts["completed"] = counts["total"] - counts["unevaluated"]
    return counts


def get_target_scope_stats(project):
    """目标级及以下条目的进度（工作台进度条使用）。"""
    scope = project.items.filter(level__lte=project.target_level)
    counts = scope.aggregate(
        total=Count("id"),
        unevaluated=Count("id", filter=Q(result=CheckItem.RESULT_UNEVALUATED)),
    )
    counts["completed"] = counts["total"] - counts["unevaluated"]
    counts["percent"] = round(counts["completed"] * 100 / counts["total"]) if counts["total"] else 0
    return counts


def get_level_states(project):
    """阶梯导航的九级状态：passed / active / unlocked / locked，以及各级统计。"""
    passed_levels = set(project.level_gates.values_list("level", flat=True))
    rows = (
        project.items.values("level")
        .annotate(
            total=Count("id"),
            unevaluated=Count("id", filter=Q(result=CheckItem.RESULT_UNEVALUATED)),
            satisfied=Count("id", filter=Q(result=CheckItem.RESULT_SATISFIED)),
            not_satisfied=Count("id", filter=Q(result=CheckItem.RESULT_NOT_SATISFIED)),
            not_applicable=Count("id", filter=Q(result=CheckItem.RESULT_NOT_APPLICABLE)),
        )
        .order_by("level")
    )
    stats_by_level = {row["level"]: row for row in rows}
    states = []
    for level in range(1, 10):
        stats = stats_by_level.get(level, {"total": 0, "unevaluated": 0, "satisfied": 0, "not_satisfied": 0, "not_applicable": 0})
        if level in passed_levels:
            state = "passed"
        elif level == project.unlocked_level:
            state = "active"
        elif level < project.unlocked_level:
            state = "unlocked"
        else:
            state = "locked"
        states.append(
            {
                "level": level,
                "state": state,
                "is_target": level == project.target_level,
                "total": stats["total"],
                "completed": stats["total"] - stats["unevaluated"],
                "unevaluated": stats["unevaluated"],
                "satisfied": stats["satisfied"],
                "not_satisfied": stats["not_satisfied"],
                "not_applicable": stats["not_applicable"],
            }
        )
    return states


@transaction.atomic
def lock_project(project, actor):
    if not can_lock_or_unlock_project(actor):
        raise PermissionDenied("当前用户不能锁定项目。")
    if project.status == EvaluationProject.STATUS_LOCKED:
        raise ValidationError("当前项目已锁定，不能重复锁定。")
    if project.status == EvaluationProject.STATUS_ARCHIVED:
        raise ValidationError("当前项目已归档，不能锁定。")
    if project.status == EvaluationProject.STATUS_REPORTED:
        raise ValidationError("当前项目已出报告，不能锁定。请先解锁后复核。")
    before = {"status": project.status}
    project.status = EvaluationProject.STATUS_LOCKED
    project.locked_by = actor
    project.locked_at = timezone.now()
    project.save(update_fields=["status", "locked_by", "locked_at", "updated_at"])
    AuditLog.objects.create(
        actor=actor,
        action="project.lock",
        model_name="EvaluationProject",
        object_id=str(project.id),
        object_repr=project.name,
        project=project,
        before=before,
        after={"status": project.status},
    )
    return project


@transaction.atomic
def unlock_project(project, actor):
    if not can_lock_or_unlock_project(actor):
        raise PermissionDenied("当前用户不能解锁项目。")
    if project.status == EvaluationProject.STATUS_IN_PROGRESS:
        raise ValidationError("当前项目正在评价中，不需要解锁。")
    if project.status == EvaluationProject.STATUS_ARCHIVED:
        raise ValidationError("当前项目已归档，不能解锁。")
    before = {"status": project.status}
    project.status = EvaluationProject.STATUS_IN_PROGRESS
    project.locked_by = None
    project.locked_at = None
    project.save(update_fields=["status", "locked_by", "locked_at", "updated_at"])
    AuditLog.objects.create(
        actor=actor,
        action="project.unlock",
        model_name="EvaluationProject",
        object_id=str(project.id),
        object_repr=project.name,
        project=project,
        before=before,
        after={"status": project.status},
    )
    return project
